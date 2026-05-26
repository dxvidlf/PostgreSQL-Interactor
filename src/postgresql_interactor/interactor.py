import json
import logging
import re
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import psycopg
from psycopg.rows import dict_row

from .exceptions import ConfigurationError, PostGISError, ValidationError
from .postgis_registry import (
    _ALL_POSTGIS_FUNCTIONS,
    _POSTGIS_CONSTRUCTORS,
    _POSTGIS_FUNCTIONS,
    _POSTGIS_SPATIAL_PREDICATES,
    validate_alias_name,
    validate_postgis_arg,
    validate_postgis_function,
)
from .postgis_types import PostGISCondition, PostGISField, PostGISKnnOrder, PostGISValue

logger = logging.getLogger(__name__)

__ALLOWED_OPERATORS = {
    "=", "<", "<=", ">", ">=", "!=", "IN", "NOT IN", "BETWEEN",
    "IS NULL", "IS NOT NULL", "LIKE", "ILIKE"
}
__ALLOWED_ORDER      = {"ASC", "DESC"}
__ALLOWED_JOIN_TYPES = {"INNER", "LEFT", "RIGHT", "FULL", "CROSS"}


class PostgreSQLInteractor:

    def __init__(
        self,
        db_name: Optional[str] = None,
        ip: Optional[str] = None,
        port: Optional[int] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> None:
        if all(v is not None for v in (db_name, ip, port, username, password)):
            self._db_name  = db_name
            self._ip       = ip
            self._port     = port
            self._username = username
            self._password = password
        else:
            try:
                from .config import get_environment_variables

                env = get_environment_variables()
            except ImportError as e:
                raise ConfigurationError(
                    "No connection parameters provided and "
                    "pydantic-settings is not available. Pass parameters "
                    "directly or install pydantic-settings."
                ) from e
            except Exception as e:
                raise ConfigurationError(
                    "No connection parameters provided and .env "
                    "configuration could not be loaded. Pass parameters "
                    "directly or create a valid .env file."
                ) from e
            self._db_name  = env.DB_NAME
            self._ip       = env.DB_IP
            self._port     = env.DB_PORT
            self._username = env.DB_USERNAME
            self._password = env.DB_PASSWORD

        self.__allowed_tables: Set[str] = set()
        self.__allowed_fields: Set[str] = set()
        self.__load_allowed_tables_and_fields()

    # ------------------------------------------------------------------
    # Getters / Setters
    # ------------------------------------------------------------------

    @property
    def ip(self) -> str:
        return self._ip

    @ip.setter
    def ip(self, ip: str) -> None:
        self._ip = ip

    @property
    def port(self) -> int:
        return self._port

    @port.setter
    def port(self, port: int) -> None:
        self._port = port

    @property
    def username(self) -> str:
        return self._username

    @username.setter
    def username(self, u: str) -> None:
        self._username = u

    @property
    def password(self) -> str:
        return self._password

    @password.setter
    def password(self, p: str) -> None:
        self._password = p

    @property
    def db_name(self) -> str:
        return self._db_name

    @db_name.setter
    def db_name(self, db: str) -> None:
        self._db_name = db

    # ------------------------------------------------------------------
    # Metadata loading
    # ------------------------------------------------------------------

    def __load_allowed_tables_and_fields(self, schema: str = "public") -> None:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = %s AND table_type = 'BASE TABLE';
                """,
                (schema,),
            )
            tables = [row["table_name"] for row in cur.fetchall()]
            self.__allowed_tables = set(tables)

            fields: Set[str] = set()
            for table in tables:
                cur.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s;
                    """,
                    (schema, table),
                )
                fields.update(row["column_name"] for row in cur.fetchall())
            self.__allowed_fields = fields

    # ------------------------------------------------------------------
    # Connection context manager
    # ------------------------------------------------------------------

    @contextmanager
    def connection(self):
        conn = psycopg.connect(
            user=self._username,
            password=self._password,
            host=self._ip,
            port=self._port,
            dbname=self._db_name,
            row_factory=dict_row,
        )
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Validators -- general
    # ------------------------------------------------------------------

    def __validate_table(self, table_name: str) -> None:
        parts = table_name.strip().split()
        base_table = parts[0]
        if base_table not in self.__allowed_tables:
            raise ValidationError(f"Table not allowed: {base_table}")
        if len(parts) > 1:
            alias = (
                parts[-1]
                if len(parts) == 2
                else parts[2]
                if len(parts) == 3 and parts[1].upper() == "AS"
                else None
            )
            if alias:
                validate_alias_name(alias)

    def __validate_field(self, field: str) -> None:
        parts = field.split(".")
        base_field = parts[-1]
        if len(parts) > 1:
            validate_alias_name(parts[0])
        if base_field not in self.__allowed_fields:
            raise ValidationError(f"Field not allowed: {base_field}")

    def __validate_fields(self, fields: List[str]) -> None:
        for f in fields:
            self.__validate_field(f)

    def __validate_operator(self, operator: str) -> None:
        if operator.upper() not in __ALLOWED_OPERATORS:
            raise ValidationError(f"Operator not allowed: {operator}")

    def __validate_order_direction(self, direction: str) -> None:
        if direction.upper() not in __ALLOWED_ORDER:
            raise ValidationError(f"ORDER BY direction not allowed: {direction}")

    def __validate_join_type(self, join_type: str) -> None:
        if join_type.upper() not in __ALLOWED_JOIN_TYPES:
            raise ValidationError(f"JOIN type not allowed: {join_type}")

    def __validate_join_on(self, on_clause: str) -> None:
        operators_pattern = "|".join(
            map(re.escape, sorted(__ALLOWED_OPERATORS, key=len, reverse=True))
        )
        pattern = (
            rf"^\s*(?P<left>[A-Za-z0-9_]+\.[A-Za-z0-9_]+)"
            rf"\s*(?P<op>{operators_pattern})\s*"
            rf"(?P<right>[A-Za-z0-9_]+\.[A-Za-z0-9_]+)\s*$"
        )
        match = re.match(pattern, on_clause, re.IGNORECASE)
        if not match:
            raise ValidationError(f"ON clause not allowed or unsafe: {on_clause}")
        for side in (match.group("left"), match.group("right")):
            validate_alias_name(side.split(".")[0])

    # ------------------------------------------------------------------
    # Renderers -- PostGIS -> SQL + parameterized values
    # ------------------------------------------------------------------

    def __render_postgis_field(
        self, pf: PostGISField, query_values: list
    ) -> str:
        validate_postgis_function(pf.function, pf.args, _POSTGIS_FUNCTIONS)
        for arg in pf.args:
            validate_postgis_arg(arg, self.__allowed_fields)

        rendered_args: List[str] = []
        for arg in pf.args:
            if isinstance(arg, (int, float)):
                rendered_args.append("%s")
                query_values.append(arg)
            else:
                rendered_args.append(arg)

        sql = f"{pf.function}({', '.join(rendered_args)})"
        if pf.alias:
            validate_alias_name(pf.alias)
            sql += f" AS {pf.alias}"
        query_values.extend(pf.values)
        return sql

    def __render_postgis_condition(
        self, pc: PostGISCondition, query_values: list
    ) -> str:
        validate_postgis_function(
            pc.function, pc.args, _POSTGIS_SPATIAL_PREDICATES
        )
        for arg in pc.args:
            validate_postgis_arg(arg, self.__allowed_fields)

        rendered_args: List[str] = []
        for arg in pc.args:
            if isinstance(arg, (int, float)):
                rendered_args.append("%s")
            else:
                rendered_args.append(arg)

        query_values.extend(pc.values)
        prefix = "NOT " if pc.negate else ""
        return f"{prefix}{pc.function}({', '.join(rendered_args)})"

    def __render_postgis_knn(self, pk: PostGISKnnOrder, query_values: list) -> str:
        validate_postgis_arg(pk.left, self.__allowed_fields)
        validate_postgis_arg(pk.right, self.__allowed_fields)
        query_values.extend(pk.values)
        return f"{pk.left} <-> {pk.right}"

    def __render_postgis_value(self, pv: PostGISValue) -> Tuple[str, list]:
        validate_postgis_function(
            pv.function, pv.args, _POSTGIS_CONSTRUCTORS
        )
        placeholders = ", ".join(["%s"] * len(pv.args))
        return f"{pv.function}({placeholders})", list(pv.args)

    # ------------------------------------------------------------------
    # Clause building
    # ------------------------------------------------------------------

    def __build_where_clause(
        self, conditions: List[Any], values: list
    ) -> str:
        where_clauses: List[str] = []

        for cond in conditions:

            if isinstance(cond, PostGISCondition):
                where_clauses.append(
                    self.__render_postgis_condition(cond, values)
                )
                continue

            field    = cond["field"]
            operator = cond["operator"].upper()
            value    = cond.get("value")

            self.__validate_field(field)
            self.__validate_operator(operator)

            if operator in ("IS NULL", "IS NOT NULL"):
                where_clauses.append(f"{field} {operator}")

            elif operator in ("IN", "NOT IN"):
                if not isinstance(value, (list, tuple)):
                    raise ValidationError(f"Value for {operator} must be a list")
                placeholders = ", ".join(["%s"] * len(value))
                where_clauses.append(f"{field} {operator} ({placeholders})")
                values.extend(value)

            elif operator == "BETWEEN":
                if not isinstance(value, (list, tuple)) or len(value) != 2:
                    raise ValidationError("BETWEEN requires a list/tuple with 2 values")
                where_clauses.append(f"{field} BETWEEN %s AND %s")
                values.extend(value)

            else:
                where_clauses.append(f"{field} {operator} %s")
                values.append(value)

        return " AND ".join(where_clauses)

    # ------------------------------------------------------------------
    # Query execution
    # ------------------------------------------------------------------

    def __execute_query(
        self,
        query: str,
        values: Optional[List[Any]] = None,
        fetch: bool = False,
    ) -> Union[List[Dict[str, Any]], Tuple[bool, int]]:
        try:
            with self.connection() as client:
                with client.cursor() as cursor:
                    cursor.execute(query, values or [])
                    if fetch:
                        return cursor.fetchall()
                    rows_affected = cursor.rowcount
                    client.commit()
                    return True, rows_affected
        except Exception as e:
            logger.error("Error executing query: %s", e)
            if fetch:
                return []
            return False, 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def select(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        self.__validate_table(params["table"])
        values: list = []

        raw_fields = params.get("fields")
        if raw_fields:
            field_parts: List[str] = []
            for f in raw_fields:
                if isinstance(f, PostGISField):
                    field_parts.append(self.__render_postgis_field(f, values))
                else:
                    self.__validate_field(f)
                    field_parts.append(f)
            fields_clause = ", ".join(field_parts)
        else:
            fields_clause = "*"

        query = f"SELECT {fields_clause} FROM {params['table']}"

        for join in params.get("joins", []):
            self.__validate_join_type(join["type"])
            self.__validate_table(join["table"])
            self.__validate_join_on(join["on"])
            query += (
                f" {join['type'].upper()} JOIN {join['table']}"
                f" ON {join['on']}"
            )

        where_conditions = params.get("filters", {}).get("where", [])
        if where_conditions:
            if isinstance(where_conditions, dict):
                where_conditions = [where_conditions]
            query += " WHERE " + self.__build_where_clause(where_conditions, values)

        group_by = params.get("filters", {}).get("group_by", [])
        if group_by:
            self.__validate_fields(group_by)
            query += " GROUP BY " + ", ".join(group_by)

        order_by = params.get("filters", {}).get("order_by", [])
        if order_by:
            order_clauses: List[str] = []
            for ob in order_by:
                direction = ob["direction"].upper()
                self.__validate_order_direction(direction)
                if "postgis" in ob:
                    rendered = self.__render_postgis_field(ob["postgis"], values)
                    rendered_no_alias = re.sub(
                        r"\s+AS\s+\w+$", "", rendered, flags=re.IGNORECASE
                    )
                    order_clauses.append(f"{rendered_no_alias} {direction}")
                elif "knn" in ob:
                    rendered = self.__render_postgis_knn(ob["knn"], values)
                    order_clauses.append(f"{rendered} {direction}")
                else:
                    self.__validate_field(ob["field"])
                    order_clauses.append(f"{ob['field']} {direction}")
            query += " ORDER BY " + ", ".join(order_clauses)

        limit = params.get("filters", {}).get("limit")
        if limit is not None:
            if not (isinstance(limit, int) and limit > 0):
                raise ValidationError("LIMIT must be a positive integer")
            query += f" LIMIT {limit}"

        return self.__execute_query(query, values, fetch=True)

    def insert(self, params: Dict[str, Any]) -> Tuple[bool, int]:
        self.__validate_table(params["table"])
        self.__validate_fields(list(params["values"].keys()))

        table       = params["table"]
        values_dict = params["values"]
        columns:      List[str] = []
        placeholders: List[str] = []
        values:       List[Any] = []

        for col, val in values_dict.items():
            columns.append(col)
            if isinstance(val, PostGISValue):
                placeholder, pg_vals = self.__render_postgis_value(val)
                placeholders.append(placeholder)
                values.extend(pg_vals)
            else:
                placeholders.append("%s")
                if isinstance(val, dict):
                    val = json.dumps(val)
                values.append(val)

        query = (
            f"INSERT INTO {table} ({', '.join(columns)})"
            f" VALUES ({', '.join(placeholders)})"
        )
        on_conflict = params.get("on_conflict")
        if on_conflict:
            query += f" ON CONFLICT ({', '.join(on_conflict)}) DO NOTHING"
        return self.__execute_query(query, values, fetch=False)

    def update(self, params: Dict[str, Any]) -> Tuple[bool, int]:
        self.__validate_table(params["table"])
        self.__validate_fields(list(params["values"].keys()))

        table      = params["table"]
        set_values = params["values"]
        filters    = params.get("filters", {}).get("where", [])

        set_clauses:     List[str] = []
        set_values_list: List[Any] = []

        for k, v in set_values.items():
            if isinstance(v, PostGISValue):
                placeholder, pg_vals = self.__render_postgis_value(v)
                set_clauses.append(f"{k} = {placeholder}")
                set_values_list.extend(pg_vals)
            else:
                set_clauses.append(f"{k} = %s")
                set_values_list.append(v)

        where_clause  = ""
        where_values: List[Any] = []
        if filters:
            if isinstance(filters, dict):
                filters = [filters]
            where_clause = " WHERE " + self.__build_where_clause(
                filters, where_values
            )

        query = f"UPDATE {table} SET {', '.join(set_clauses)}{where_clause}"
        return self.__execute_query(
            query, set_values_list + where_values, fetch=False
        )

    def insert_many(
        self, params: Dict[str, Any], records: List[Dict[str, Any]]
    ) -> Tuple[bool, int]:
        if not records:
            logger.warning("insert_many called with empty record list.")
            return True, 0

        self.__validate_table(params["table"])
        table = params["table"]

        columns: List[str] = list(dict.fromkeys(k for r in records for k in r))
        self.__validate_fields(columns)

        has_postgis = any(
            isinstance(v, PostGISValue)
            for r in records
            for v in r.values()
        )

        on_conflict = params.get("on_conflict")
        conflict_clause = ""
        if on_conflict:
            conflict_clause = f" ON CONFLICT ({', '.join(on_conflict)}) DO NOTHING"

        total_rows = 0
        try:
            with self.connection() as client:
                with client.cursor() as cursor:
                    if not has_postgis:
                        placeholders = ", ".join(["%s"] * len(columns))
                        query = (
                            f"INSERT INTO {table} ({', '.join(columns)})"
                            f" VALUES ({placeholders})"
                            f"{conflict_clause}"
                        )
                        for record in records:
                            row = []
                            for col in columns:
                                val = record.get(col)
                                if isinstance(val, dict):
                                    val = json.dumps(val)
                                row.append(val)
                            cursor.execute(query, row)
                            total_rows += cursor.rowcount
                    else:
                        for record in records:
                            row_placeholders: List[str] = []
                            row_values:       List[Any]  = []
                            for col in columns:
                                val = record.get(col)
                                if isinstance(val, PostGISValue):
                                    ph, pg_vals = self.__render_postgis_value(val)
                                    row_placeholders.append(ph)
                                    row_values.extend(pg_vals)
                                elif isinstance(val, dict):
                                    row_placeholders.append("%s")
                                    row_values.append(json.dumps(val))
                                else:
                                    row_placeholders.append("%s")
                                    row_values.append(val)
                            query = (
                                f"INSERT INTO {table} ({', '.join(columns)})"
                                f" VALUES ({', '.join(row_placeholders)})"
                                f"{conflict_clause}"
                            )
                            cursor.execute(query, row_values)
                            total_rows += cursor.rowcount

                    client.commit()
                    return True, total_rows
        except Exception as e:
            logger.error("Error in insert_many: %s", e)
            return False, 0

    def update_many(
        self, params: Dict[str, Any], records: List[Dict[str, Any]]
    ) -> Tuple[bool, int]:
        if not records:
            logger.warning("update_many called with empty record list.")
            return True, 0

        self.__validate_table(params["table"])
        table       = params["table"]
        value_keys  = params["value_keys"]
        filter_keys = params["filter_keys"]

        self.__validate_fields(value_keys)
        self.__validate_fields(filter_keys)

        total_rows = 0
        try:
            with self.connection() as client:
                with client.cursor() as cursor:
                    for record in records:
                        set_clauses:     List[str] = []
                        set_values_list: List[Any] = []

                        for k in value_keys:
                            v = record.get(k)
                            if isinstance(v, PostGISValue):
                                ph, pg_vals = self.__render_postgis_value(v)
                                set_clauses.append(f"{k} = {ph}")
                                set_values_list.extend(pg_vals)
                            else:
                                set_clauses.append(f"{k} = %s")
                                set_values_list.append(v)

                        where_values: List[Any] = []
                        where_conditions: List[Any] = []
                        for k in filter_keys:
                            v = record.get(k)
                            if isinstance(v, PostGISCondition):
                                where_conditions.append(v)
                            else:
                                where_conditions.append({
                                    "field":    k,
                                    "operator": "=",
                                    "value":    v,
                                })

                        where_clause = " WHERE " + self.__build_where_clause(
                            where_conditions, where_values
                        )
                        query = (
                            f"UPDATE {table}"
                            f" SET {', '.join(set_clauses)}"
                            f"{where_clause}"
                        )
                        cursor.execute(query, set_values_list + where_values)
                        total_rows += cursor.rowcount

                    client.commit()
                    return True, total_rows
        except Exception as e:
            logger.error("Error in update_many: %s", e)
            return False, 0

    def delete(self, params: Dict[str, Any]) -> Tuple[bool, int]:
        self.__validate_table(params["table"])

        filters = params.get("filters", {}).get("where", [])
        if not filters:
            raise ValidationError(
                "DELETE without WHERE clause is not allowed. "
                "Provide at least one condition in 'filters.where'."
            )

        where_values: List[Any] = []
        if isinstance(filters, dict):
            filters = [filters]
        where_clause = " WHERE " + self.__build_where_clause(filters, where_values)

        query = f"DELETE FROM {params['table']}{where_clause}"
        return self.__execute_query(query, where_values, fetch=False)
