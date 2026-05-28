import json
import logging
import re
from contextlib import contextmanager
from typing import Any, Dict, Generator, List, Optional, Set, Tuple, Union

import psycopg
from psycopg.rows import dict_row

from .exceptions import ConfigurationError, PostGISError, QueryExecutionError, ValidationError
from .postgis_registry import (
    _POSTGIS_CONSTRUCTORS,
    _POSTGIS_FUNCTIONS,
    _POSTGIS_SPATIAL_PREDICATES,
    validate_alias_name,
    validate_postgis_arg,
    validate_postgis_function,
)
from .postgis_types import PostGISCondition, PostGISField, PostGISKnnOrder, PostGISValue
from .schemas import (
    DeleteParams,
    ExistsCondition,
    Filters,
    InsertManyParams,
    InsertParams,
    JoinClause,
    OrderByClause,
    SelectParams,
    Subquery,
    SubqueryCondition,
    UpdateManyParams,
    UpdateParams,
    WhereCondition,
)

logger = logging.getLogger(__name__)

_ALLOWED_OPERATORS = {
    "=", "<", "<=", ">", ">=", "!=", "IN", "NOT IN", "BETWEEN",
    "IS NULL", "IS NOT NULL", "LIKE", "ILIKE",
}
_ALLOWED_ORDER      = {"ASC", "DESC"}
_ALLOWED_JOIN_TYPES = {"INNER", "LEFT", "RIGHT", "FULL", "CROSS"}


class PostgreSQLInteractor:
    """
    Secure PostgreSQL query builder and executor with PostGIS support.

    Every query parameter is validated against the live database schema before
    execution.  Table names, column names, operators, and aliases are checked
    against an allow-list derived from ``information_schema`` at construction
    time, making SQL injection structurally impossible through normal usage.

    Spatial operations are further constrained to a PostGIS function whitelist
    defined in :mod:`.postgis_registry`.

    Connection parameters can be supplied directly or loaded automatically
    from a ``.env`` file (requires the ``pydantic-settings`` extra).

    All public query methods accept either a typed schema object (e.g.
    :class:`~.schemas.SelectParams`) **or** a plain ``dict`` with the same
    keys — the dict is coerced via ``model_validate`` so existing call-sites
    need not change.

    Errors fall into two categories:

    - :class:`~.exceptions.ValidationError` / :class:`~.exceptions.PostGISError`
      — raised *before* a connection is opened (bad input).
    - :class:`~.exceptions.QueryExecutionError` — raised when the database
      returns an error during execution.
    """

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
                    "No connection parameters provided and pydantic-settings is "
                    "not available.  Pass parameters directly or install it with: "
                    "pip install postgresql-interactor[pydantic]"
                ) from e
            except Exception as e:
                raise ConfigurationError(
                    "No connection parameters provided and the .env file could "
                    "not be loaded.  Pass parameters directly or create a valid "
                    ".env file."
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
    # Properties
    # ------------------------------------------------------------------

    @property
    def ip(self) -> str:
        return self._ip

    @ip.setter
    def ip(self, value: str) -> None:
        self._ip = value

    @property
    def port(self) -> int:
        return self._port

    @port.setter
    def port(self, value: int) -> None:
        self._port = value

    @property
    def username(self) -> str:
        return self._username

    @username.setter
    def username(self, value: str) -> None:
        self._username = value

    @property
    def password(self) -> str:
        return self._password

    @password.setter
    def password(self, value: str) -> None:
        self._password = value

    @property
    def db_name(self) -> str:
        return self._db_name

    @db_name.setter
    def db_name(self, value: str) -> None:
        self._db_name = value

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def __load_allowed_tables_and_fields(self, schema: str = "public") -> None:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = %s AND table_type = 'BASE TABLE'
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
                    WHERE table_schema = %s AND table_name = %s
                    """,
                    (schema, table),
                )
                fields.update(row["column_name"] for row in cur.fetchall())
            self.__allowed_fields = fields

    def reload_schema(self, schema: str = "public") -> None:
        """Reload the table/column allow-lists from the database.

        Call this after running migrations or adding columns so the interactor
        picks up the new schema without creating a new instance.

        Args:
            schema: PostgreSQL schema to inspect.  Defaults to ``"public"``.
        """
        self.__load_allowed_tables_and_fields(schema)

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    @contextmanager
    def connection(self) -> Generator:
        """Open a database connection and yield it as a context manager.

        The connection is automatically rolled back on exception and closed
        when the ``with`` block exits.

        Example::

            with interactor.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
        """
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
    # Validators
    # ------------------------------------------------------------------

    def __validate_table(self, table_name: str) -> None:
        parts = table_name.strip().split()
        base_table = parts[0]
        if base_table not in self.__allowed_tables:
            raise ValidationError(f"Table not allowed: {base_table!r}")
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
            raise ValidationError(f"Field not allowed: {base_field!r}")

    def __validate_fields(self, fields: List[str]) -> None:
        for f in fields:
            self.__validate_field(f)

    def __validate_operator(self, operator: str) -> None:
        if operator.upper() not in _ALLOWED_OPERATORS:
            raise ValidationError(f"Operator not allowed: {operator!r}")

    def __validate_order_direction(self, direction: str) -> None:
        if direction.upper() not in _ALLOWED_ORDER:
            raise ValidationError(f"ORDER BY direction not allowed: {direction!r}")

    def __validate_join_type(self, join_type: str) -> None:
        if join_type.upper() not in _ALLOWED_JOIN_TYPES:
            raise ValidationError(f"JOIN type not allowed: {join_type!r}")

    def __validate_join_on(self, on_clause: str) -> None:
        operators_pattern = "|".join(
            map(re.escape, sorted(_ALLOWED_OPERATORS, key=len, reverse=True))
        )
        pattern = (
            rf"^\s*(?P<left>[A-Za-z0-9_]+\.[A-Za-z0-9_]+)"
            rf"\s*(?P<op>{operators_pattern})\s*"
            rf"(?P<right>[A-Za-z0-9_]+\.[A-Za-z0-9_]+)\s*$"
        )
        match = re.match(pattern, on_clause, re.IGNORECASE)
        if not match:
            raise ValidationError(f"ON clause not allowed or unsafe: {on_clause!r}")
        for side in (match.group("left"), match.group("right")):
            validate_alias_name(side.split(".")[0])

    # ------------------------------------------------------------------
    # PostGIS renderers
    # ------------------------------------------------------------------

    def __render_postgis_field(self, pf: PostGISField, values: list) -> str:
        validate_postgis_function(pf.function, pf.args, _POSTGIS_FUNCTIONS)
        for arg in pf.args:
            validate_postgis_arg(arg, self.__allowed_fields)

        rendered_args: List[str] = []
        for arg in pf.args:
            if isinstance(arg, (int, float)):
                rendered_args.append("%s")
                values.append(arg)
            else:
                rendered_args.append(arg)

        sql = f"{pf.function}({', '.join(rendered_args)})"
        if pf.alias:
            validate_alias_name(pf.alias)
            sql += f" AS {pf.alias}"
        values.extend(pf.values)
        return sql

    def __render_postgis_condition(self, pc: PostGISCondition, values: list) -> str:
        validate_postgis_function(pc.function, pc.args, _POSTGIS_SPATIAL_PREDICATES)
        for arg in pc.args:
            validate_postgis_arg(arg, self.__allowed_fields)

        rendered_args: List[str] = []
        for arg in pc.args:
            if isinstance(arg, (int, float)):
                rendered_args.append("%s")
            else:
                rendered_args.append(arg)

        values.extend(pc.values)
        prefix = "NOT " if pc.negate else ""
        return f"{prefix}{pc.function}({', '.join(rendered_args)})"

    def __render_postgis_knn(self, pk: PostGISKnnOrder, values: list) -> str:
        validate_postgis_arg(pk.left, self.__allowed_fields)
        validate_postgis_arg(pk.right, self.__allowed_fields)
        values.extend(pk.values)
        return f"{pk.left} <-> {pk.right}"

    def __render_postgis_value(self, pv: PostGISValue) -> Tuple[str, List[Any]]:
        validate_postgis_function(pv.function, pv.args, _POSTGIS_CONSTRUCTORS)
        placeholders = ", ".join(["%s"] * len(pv.args))
        return f"{pv.function}({placeholders})", list(pv.args)

    # ------------------------------------------------------------------
    # SQL clause builders
    # ------------------------------------------------------------------

    def __build_where_clause(self, conditions: List[Any], values: list) -> str:
        """Build the body of a WHERE clause (without the ``WHERE`` keyword).

        Appends parameterized values to *values* in the same order they appear
        in the returned SQL string.
        """
        parts: List[str] = []

        for cond in conditions:
            # Normalize plain dicts to WhereCondition
            if isinstance(cond, dict) and "field" in cond:
                cond = WhereCondition(**cond)

            if isinstance(cond, PostGISCondition):
                parts.append(self.__render_postgis_condition(cond, values))

            elif isinstance(cond, SubqueryCondition):
                self.__validate_field(cond.field)
                self.__validate_operator(cond.operator)
                sub_sql = self.__build_select_sql(cond.subquery.params, values)
                parts.append(f"{cond.field} {cond.operator.upper()} ({sub_sql})")

            elif isinstance(cond, ExistsCondition):
                sub_sql = self.__build_select_sql(cond.subquery.params, values)
                prefix = "NOT EXISTS" if cond.negate else "EXISTS"
                parts.append(f"{prefix} ({sub_sql})")

            elif isinstance(cond, WhereCondition):
                field    = cond.field
                operator = cond.operator.upper()
                value    = cond.value

                self.__validate_field(field)
                self.__validate_operator(operator)

                if operator in ("IS NULL", "IS NOT NULL"):
                    parts.append(f"{field} {operator}")

                elif operator in ("IN", "NOT IN"):
                    if not isinstance(value, (list, tuple)):
                        raise ValidationError(f"Value for {operator} must be a list or tuple")
                    placeholders = ", ".join(["%s"] * len(value))
                    parts.append(f"{field} {operator} ({placeholders})")
                    values.extend(
                        json.dumps(v) if isinstance(v, dict) else v for v in value
                    )

                elif operator == "BETWEEN":
                    if not isinstance(value, (list, tuple)) or len(value) != 2:
                        raise ValidationError("BETWEEN requires exactly two values as a list")
                    parts.append(f"{field} BETWEEN %s AND %s")
                    values.extend(
                        json.dumps(v) if isinstance(v, dict) else v for v in value
                    )

                else:
                    parts.append(f"{field} {operator} %s")
                    values.append(json.dumps(value) if isinstance(value, dict) else value)

            else:
                raise ValidationError(
                    f"Unsupported condition type: {type(cond).__name__}"
                )

        return " AND ".join(parts)

    def __build_select_sql(self, params: SelectParams, values: list) -> str:
        """Build a complete SELECT SQL string, mutating *values* with parameters.

        Called recursively for subqueries — both derived-table sources and
        subquery conditions in WHERE.
        """
        # FROM clause
        if isinstance(params.table, Subquery):
            validate_alias_name(params.table.alias)
            sub_sql = self.__build_select_sql(params.table.params, values)
            from_clause = f"({sub_sql}) AS {params.table.alias}"
        else:
            self.__validate_table(params.table)
            from_clause = params.table

        # SELECT list
        if params.fields:
            field_parts: List[str] = []
            for f in params.fields:
                if isinstance(f, PostGISField):
                    field_parts.append(self.__render_postgis_field(f, values))
                else:
                    self.__validate_field(f)
                    field_parts.append(f)
            select_clause = ", ".join(field_parts)
        else:
            select_clause = "*"

        sql = f"SELECT {select_clause} FROM {from_clause}"

        # JOINs
        for join in params.joins or []:
            if isinstance(join, dict):
                join = JoinClause(**join)
            self.__validate_join_type(join.type)
            self.__validate_table(join.table)
            self.__validate_join_on(join.on)
            sql += f" {join.type.upper()} JOIN {join.table} ON {join.on}"

        # WHERE / GROUP BY / ORDER BY / LIMIT / OFFSET
        sql += self.__build_filter_clauses(params.filters, values)
        return sql

    def __build_filter_clauses(
        self, filters: Optional[Filters], values: list
    ) -> str:
        """Build the optional clauses that follow FROM/JOIN for a SELECT query.

        Returns a string that may include WHERE, GROUP BY, ORDER BY, LIMIT,
        and OFFSET, all prefixed with the appropriate whitespace.
        """
        if not filters:
            return ""

        sql = ""

        if filters.where:
            sql += " WHERE " + self.__build_where_clause(filters.where, values)

        if filters.group_by:
            self.__validate_fields(filters.group_by)
            sql += " GROUP BY " + ", ".join(filters.group_by)

        if filters.order_by:
            order_parts: List[str] = []
            for ob in filters.order_by:
                if isinstance(ob, dict):
                    ob = OrderByClause(**ob)
                self.__validate_order_direction(ob.direction)
                if ob.postgis is not None:
                    rendered = self.__render_postgis_field(ob.postgis, values)
                    rendered_no_alias = re.sub(
                        r"\s+AS\s+\w+$", "", rendered, flags=re.IGNORECASE
                    )
                    order_parts.append(f"{rendered_no_alias} {ob.direction}")
                elif ob.knn is not None:
                    rendered = self.__render_postgis_knn(ob.knn, values)
                    order_parts.append(f"{rendered} {ob.direction}")
                else:
                    self.__validate_field(ob.field)
                    order_parts.append(f"{ob.field} {ob.direction}")
            sql += " ORDER BY " + ", ".join(order_parts)

        if filters.limit is not None:
            sql += f" LIMIT {filters.limit}"

        if filters.offset is not None:
            sql += f" OFFSET {filters.offset}"

        return sql

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
            with self.connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(query, values or [])
                    if fetch:
                        return cursor.fetchall()
                    rows_affected = cursor.rowcount
                    conn.commit()
                    return True, rows_affected
        except (ValidationError, PostGISError):
            raise
        except Exception as e:
            logger.error("Query execution failed: %s", e)
            raise QueryExecutionError(str(e)) from e

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def select(
        self,
        params: Union[SelectParams, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Execute a SELECT query and return all matching rows.

        Args:
            params: A :class:`~.schemas.SelectParams` instance **or** a plain
                dict with the same keys:

                - ``table`` *(str | Subquery)* — table name with optional alias
                  (``"users u"`` / ``"users AS u"``) or a
                  :class:`~.schemas.Subquery` for derived-table queries.
                - ``fields`` *(str | PostGISField | list, optional)* — column(s)
                  to return.  Omit or pass ``None`` for ``SELECT *``.
                - ``joins`` *(JoinClause | dict | list, optional)* — one or more
                  JOIN specifications.
                - ``filters`` *(Filters | dict, optional)* — ``where``,
                  ``group_by``, ``order_by``, ``limit``, ``offset``.

        Returns:
            A list of row dicts.

        Raises:
            ValidationError: Invalid table, field, operator, or alias.
            PostGISError: Forbidden or invalid PostGIS function.
            QueryExecutionError: Database-level execution failure.

        Example::

            rows = db.select({
                "table": "users u",
                "fields": ["u.id", "u.name"],
                "filters": {
                    "where": {"field": "u.active", "operator": "=", "value": True},
                    "order_by": {"field": "u.name", "direction": "ASC"},
                    "limit": 20,
                    "offset": 40,
                },
            })
        """
        if isinstance(params, dict):
            params = SelectParams.model_validate(params)
        values: list = []
        query = self.__build_select_sql(params, values)
        return self.__execute_query(query, values, fetch=True)

    def insert(
        self,
        params: Union[InsertParams, Dict[str, Any]],
    ) -> Tuple[bool, int]:
        """Execute a single-row INSERT.

        Args:
            params: An :class:`~.schemas.InsertParams` instance or dict with:

                - ``table`` *(str)* — target table.
                - ``values`` *(dict)* — column → value mapping.  Use a
                  :class:`~.postgis_types.PostGISValue` for geometry columns.
                  ``dict`` values are automatically serialised to JSON.
                - ``on_conflict`` *(str | list, optional)* — column(s) that
                  trigger ``ON CONFLICT (…) DO NOTHING``.

        Returns:
            ``(True, rows_inserted)``.

        Raises:
            ValidationError: Invalid table or field name.
            QueryExecutionError: Database-level execution failure.

        Example::

            ok, n = db.insert({
                "table": "users",
                "values": {"name": "Alice", "age": 30},
                "on_conflict": "email",
            })
        """
        if isinstance(params, dict):
            params = InsertParams.model_validate(params)

        self.__validate_table(params.table)
        self.__validate_fields(list(params.values.keys()))

        columns:      List[str] = []
        placeholders: List[str] = []
        values:       List[Any] = []

        for col, val in params.values.items():
            columns.append(col)
            if isinstance(val, PostGISValue):
                ph, pg_vals = self.__render_postgis_value(val)
                placeholders.append(ph)
                values.extend(pg_vals)
            else:
                placeholders.append("%s")
                values.append(json.dumps(val) if isinstance(val, dict) else val)

        query = (
            f"INSERT INTO {params.table} ({', '.join(columns)})"
            f" VALUES ({', '.join(placeholders)})"
        )
        if params.on_conflict:
            query += f" ON CONFLICT ({', '.join(params.on_conflict)}) DO NOTHING"

        return self.__execute_query(query, values)

    def update(
        self,
        params: Union[UpdateParams, Dict[str, Any]],
    ) -> Tuple[bool, int]:
        """Execute a single UPDATE query.

        Args:
            params: An :class:`~.schemas.UpdateParams` instance or dict with:

                - ``table`` *(str)* — target table.
                - ``values`` *(dict)* — column → value mapping for the SET
                  clause.  Accepts :class:`~.postgis_types.PostGISValue`.
                - ``filters`` *(Filters | dict, optional)* — WHERE conditions.
                  Omitting ``filters`` updates **all rows** in the table.

        Returns:
            ``(True, rows_updated)``.

        Raises:
            ValidationError: Invalid table or field name.
            QueryExecutionError: Database-level execution failure.

        Example::

            ok, n = db.update({
                "table": "users",
                "values": {"active": False},
                "filters": {"where": {"field": "id", "operator": "=", "value": 7}},
            })
        """
        if isinstance(params, dict):
            params = UpdateParams.model_validate(params)

        self.__validate_table(params.table)
        self.__validate_fields(list(params.values.keys()))

        set_clauses: List[str] = []
        set_values:  List[Any] = []

        for col, val in params.values.items():
            if isinstance(val, PostGISValue):
                ph, pg_vals = self.__render_postgis_value(val)
                set_clauses.append(f"{col} = {ph}")
                set_values.extend(pg_vals)
            else:
                set_clauses.append(f"{col} = %s")
                set_values.append(json.dumps(val) if isinstance(val, dict) else val)

        where_values: List[Any] = []
        where_sql = ""
        if params.filters and params.filters.where:
            where_sql = " WHERE " + self.__build_where_clause(
                params.filters.where, where_values
            )

        query = f"UPDATE {params.table} SET {', '.join(set_clauses)}{where_sql}"
        return self.__execute_query(query, set_values + where_values)

    def insert_many(
        self,
        params: Union[InsertManyParams, Dict[str, Any]],
        records: List[Dict[str, Any]],
    ) -> Tuple[bool, int]:
        """Execute a batch INSERT for multiple records.

        Columns are inferred from the union of keys across all records.
        Rows with missing columns receive ``NULL``.

        Args:
            params: An :class:`~.schemas.InsertManyParams` instance or dict with:

                - ``table`` *(str)* — target table.
                - ``on_conflict`` *(str | list, optional)* — conflict column(s)
                  for ``ON CONFLICT (…) DO NOTHING``.
            records: List of dicts mapping column name → value.  Geometry
                columns accept :class:`~.postgis_types.PostGISValue`.

        Returns:
            ``(True, total_rows_inserted)``.

        Raises:
            ValidationError: Invalid table or column name.
            QueryExecutionError: Database-level execution failure.

        Example::

            ok, n = db.insert_many(
                {"table": "products"},
                [{"name": "A", "price": 10}, {"name": "B", "price": 20}],
            )
        """
        if not records:
            logger.warning("insert_many called with empty record list")
            return True, 0

        if isinstance(params, dict):
            params = InsertManyParams.model_validate(params)

        self.__validate_table(params.table)
        columns: List[str] = list(dict.fromkeys(k for r in records for k in r))
        self.__validate_fields(columns)

        has_postgis = any(
            isinstance(v, PostGISValue) for r in records for v in r.values()
        )
        conflict_clause = (
            f" ON CONFLICT ({', '.join(params.on_conflict)}) DO NOTHING"
            if params.on_conflict
            else ""
        )

        total = 0
        try:
            with self.connection() as conn:
                with conn.cursor() as cursor:
                    if not has_postgis:
                        placeholders = ", ".join(["%s"] * len(columns))
                        query = (
                            f"INSERT INTO {params.table} ({', '.join(columns)})"
                            f" VALUES ({placeholders}){conflict_clause}"
                        )
                        for record in records:
                            row = [
                                json.dumps(v) if isinstance(v := record.get(col), dict) else v
                                for col in columns
                            ]
                            cursor.execute(query, row)
                            total += cursor.rowcount
                    else:
                        for record in records:
                            row_ph:  List[str] = []
                            row_val: List[Any] = []
                            for col in columns:
                                val = record.get(col)
                                if isinstance(val, PostGISValue):
                                    ph, pg_vals = self.__render_postgis_value(val)
                                    row_ph.append(ph)
                                    row_val.extend(pg_vals)
                                elif isinstance(val, dict):
                                    row_ph.append("%s")
                                    row_val.append(json.dumps(val))
                                else:
                                    row_ph.append("%s")
                                    row_val.append(val)
                            query = (
                                f"INSERT INTO {params.table} ({', '.join(columns)})"
                                f" VALUES ({', '.join(row_ph)}){conflict_clause}"
                            )
                            cursor.execute(query, row_val)
                            total += cursor.rowcount

                conn.commit()
                return True, total
        except (ValidationError, PostGISError):
            raise
        except Exception as e:
            logger.error("insert_many failed: %s", e)
            raise QueryExecutionError(str(e)) from e

    def update_many(
        self,
        params: Union[UpdateManyParams, Dict[str, Any]],
        records: List[Dict[str, Any]],
    ) -> Tuple[bool, int]:
        """Execute a batch UPDATE — one statement per record.

        Args:
            params: An :class:`~.schemas.UpdateManyParams` instance or dict with:

                - ``table`` *(str)* — target table.
                - ``value_keys`` *(str | list)* — column(s) to write in SET.
                - ``filter_keys`` *(str | list)* — column(s) used in WHERE to
                  identify each row.
            records: List of dicts that must contain all ``value_keys`` and
                ``filter_keys``.  Filter values may be plain scalars or
                :class:`~.postgis_types.PostGISCondition` objects.

        Returns:
            ``(True, total_rows_updated)``.

        Raises:
            ValidationError: Invalid table or column name.
            QueryExecutionError: Database-level execution failure.

        Example::

            ok, n = db.update_many(
                {"table": "inventory", "value_keys": "stock", "filter_keys": "sku"},
                [{"sku": "A1", "stock": 5}, {"sku": "B2", "stock": 12}],
            )
        """
        if not records:
            logger.warning("update_many called with empty record list")
            return True, 0

        if isinstance(params, dict):
            params = UpdateManyParams.model_validate(params)

        self.__validate_table(params.table)
        self.__validate_fields(params.value_keys)
        self.__validate_fields(params.filter_keys)

        total = 0
        try:
            with self.connection() as conn:
                with conn.cursor() as cursor:
                    for record in records:
                        set_clauses: List[str] = []
                        set_values:  List[Any] = []

                        for col in params.value_keys:
                            val = record.get(col)
                            if isinstance(val, PostGISValue):
                                ph, pg_vals = self.__render_postgis_value(val)
                                set_clauses.append(f"{col} = {ph}")
                                set_values.extend(pg_vals)
                            else:
                                set_clauses.append(f"{col} = %s")
                                set_values.append(json.dumps(val) if isinstance(val, dict) else val)

                        where_conditions: List[Any] = [
                            val
                            if isinstance(val := record.get(col), PostGISCondition)
                            else WhereCondition(field=col, operator="=", value=val)
                            for col in params.filter_keys
                        ]
                        where_values: List[Any] = []
                        where_sql = self.__build_where_clause(where_conditions, where_values)

                        cursor.execute(
                            f"UPDATE {params.table}"
                            f" SET {', '.join(set_clauses)}"
                            f" WHERE {where_sql}",
                            set_values + where_values,
                        )
                        total += cursor.rowcount

                conn.commit()
                return True, total
        except (ValidationError, PostGISError):
            raise
        except Exception as e:
            logger.error("update_many failed: %s", e)
            raise QueryExecutionError(str(e)) from e

    def delete(
        self,
        params: Union[DeleteParams, Dict[str, Any]],
    ) -> Tuple[bool, int]:
        """Execute a DELETE query.

        A WHERE clause is **mandatory**.  Attempting to delete without any
        conditions raises :class:`~.exceptions.ValidationError` immediately,
        before a connection is opened.

        Args:
            params: A :class:`~.schemas.DeleteParams` instance or dict with:

                - ``table`` *(str)* — target table.
                - ``filters`` *(Filters | dict)* — must include at least one
                  ``where`` condition.

        Returns:
            ``(True, rows_deleted)``.

        Raises:
            ValidationError: No WHERE conditions, or invalid table/field.
            QueryExecutionError: Database-level execution failure.

        Example::

            ok, n = db.delete({
                "table": "sessions",
                "filters": {"where": {"field": "expired", "operator": "=", "value": True}},
            })
        """
        if isinstance(params, dict):
            params = DeleteParams.model_validate(params)

        self.__validate_table(params.table)

        if not params.filters.where:
            raise ValidationError(
                "DELETE without WHERE is not allowed.  "
                "Provide at least one condition in 'filters.where'."
            )

        where_values: List[Any] = []
        where_clause = " WHERE " + self.__build_where_clause(
            params.filters.where, where_values
        )
        return self.__execute_query(
            f"DELETE FROM {params.table}{where_clause}", where_values
        )
