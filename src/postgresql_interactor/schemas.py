"""
Pydantic v2 parameter schemas for every public method of PostgreSQLInteractor.

All schemas accept flexible input where it makes sense — for example, a single
string is coerced to a one-element list, and a bare dict condition is coerced to
a :class:`WhereCondition`.  This means callers never have to wrap a single value
in a list just to satisfy the type.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .postgis_types import PostGISCondition, PostGISField, PostGISKnnOrder, PostGISValue

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ARBITRARY = ConfigDict(arbitrary_types_allowed=True)


# ---------------------------------------------------------------------------
# WHERE conditions
# ---------------------------------------------------------------------------


class WhereCondition(BaseModel):
    """
    A standard SQL WHERE condition.

    Example::

        WhereCondition(field="age", operator=">", value=18)
        WhereCondition(field="status", operator="IN", value=["active", "pending"])
        WhereCondition(field="deleted_at", operator="IS NULL")
    """

    field: str
    operator: str
    value: Optional[Any] = None


class SubqueryCondition(BaseModel):
    """
    WHERE condition whose right-hand side is a correlated subquery.

    Example::

        SubqueryCondition(
            field="department_id",
            operator="IN",
            subquery=Subquery(
                params=SelectParams(table="departments", filters=Filters(
                    where=WhereCondition(field="active", operator="=", value=True)
                )),
                alias="d",
            ),
        )
    """

    model_config = _ARBITRARY

    field: str
    operator: str
    subquery: Subquery  # resolved after model_rebuild()


class ExistsCondition(BaseModel):
    """
    WHERE [NOT] EXISTS (SELECT ...) condition.

    Example::

        ExistsCondition(
            subquery=Subquery(
                params=SelectParams(table="orders", filters=Filters(
                    where=WhereCondition(field="user_id", operator="=", value=42)
                )),
                alias="o",
            ),
            negate=True,
        )
    """

    model_config = _ARBITRARY

    subquery: Subquery  # resolved after model_rebuild()
    negate: bool = False


# ---------------------------------------------------------------------------
# JOIN
# ---------------------------------------------------------------------------


class JoinClause(BaseModel):
    """
    A single JOIN clause.

    Example::

        JoinClause(type="LEFT", table="orders o", on="u.id = o.user_id")
        # dict shorthand also accepted:
        {"type": "inner", "table": "roles r", "on": "u.role_id = r.id"}
    """

    type: str = "INNER"
    table: str
    on: str

    @field_validator("type", mode="before")
    @classmethod
    def _upper_type(cls, v: str) -> str:
        return v.upper()


# ---------------------------------------------------------------------------
# ORDER BY
# ---------------------------------------------------------------------------


class OrderByClause(BaseModel):
    """
    An ORDER BY specification.

    Exactly one of ``field``, ``postgis``, or ``knn`` must be supplied.

    Example::

        OrderByClause(field="created_at", direction="DESC")
        OrderByClause(postgis=PostGISField("ST_Area", ["geom"]), direction="ASC")
        OrderByClause(knn=PostGISKnnOrder("geom", "ST_MakePoint(%s,%s)", values=[lng, lat]))
    """

    model_config = _ARBITRARY

    field: Optional[str] = None
    direction: str = "ASC"
    postgis: Optional[PostGISField] = None
    knn: Optional[PostGISKnnOrder] = None

    @field_validator("direction", mode="before")
    @classmethod
    def _upper_dir(cls, v: str) -> str:
        return v.upper()

    @model_validator(mode="after")
    def _require_one(self) -> OrderByClause:
        if not any([self.field, self.postgis, self.knn]):
            raise ValueError(
                "OrderByClause requires at least one of: 'field', 'postgis', 'knn'"
            )
        return self


# ---------------------------------------------------------------------------
# Filters (shared by SELECT, UPDATE, DELETE)
# ---------------------------------------------------------------------------

_AnyCondition = Union[
    WhereCondition,
    PostGISCondition,
    SubqueryCondition,
    ExistsCondition,
    Dict[str, Any],
]


class Filters(BaseModel):
    """
    Optional clauses applied after FROM/JOIN in a query.

    All list fields accept a single item as a shorthand — it is coerced to a
    one-element list automatically.

    Fields:

    - ``where``    — one or more WHERE conditions (see note below).
    - ``group_by`` — column name(s) for GROUP BY.
    - ``order_by`` — one or more :class:`OrderByClause` (or dicts).
    - ``limit``    — maximum rows to return (positive integer).
    - ``offset``   — number of rows to skip (non-negative integer).

    ``where`` accepts any mix of :class:`WhereCondition`,
    :class:`~.postgis_types.PostGISCondition`, :class:`SubqueryCondition`,
    :class:`ExistsCondition`, or plain dicts with ``field``/``operator``/``value``
    keys.
    """

    model_config = _ARBITRARY

    where: Optional[List[Any]] = None
    group_by: Optional[List[str]] = None
    order_by: Optional[List[Any]] = None
    limit: Optional[int] = Field(default=None, gt=0)
    offset: Optional[int] = Field(default=None, ge=0)

    @field_validator("where", mode="before")
    @classmethod
    def _coerce_where(cls, v: Any) -> Any:
        if v is None:
            return None
        if not isinstance(v, list):
            return [v]
        return v

    @field_validator("group_by", mode="before")
    @classmethod
    def _coerce_group_by(cls, v: Any) -> Any:
        if isinstance(v, str):
            return [v]
        return v

    @field_validator("order_by", mode="before")
    @classmethod
    def _coerce_order_by(cls, v: Any) -> Any:
        if v is None:
            return None
        if not isinstance(v, list):
            return [v]
        return v


# ---------------------------------------------------------------------------
# Subquery (derived table)
# ---------------------------------------------------------------------------


class Subquery(BaseModel):
    """
    A derived-table subquery used in a FROM clause or as a condition RHS.

    ``alias`` is required because the SQL engine needs a name to reference the
    subquery's columns.

    Example::

        Subquery(
            params=SelectParams(
                table="orders",
                fields=["user_id", "COUNT(*) AS order_count"],
                filters=Filters(group_by="user_id"),
            ),
            alias="order_stats",
        )
    """

    model_config = _ARBITRARY

    params: SelectParams  # resolved after model_rebuild()
    alias: str


# ---------------------------------------------------------------------------
# SELECT
# ---------------------------------------------------------------------------


class SelectParams(BaseModel):
    """
    Parameters for a SELECT query.

    ``fields`` and ``joins`` both accept a single item or a list — single
    strings / objects are coerced to one-element lists automatically.

    Example::

        # Simple
        SelectParams(table="users", fields="name")

        # With filters
        SelectParams(
            table="users u",
            fields=["u.id", "u.name", "r.label"],
            joins=JoinClause(type="LEFT", table="roles r", on="u.role_id = r.id"),
            filters=Filters(
                where=WhereCondition(field="u.active", operator="=", value=True),
                order_by=OrderByClause(field="u.name", direction="ASC"),
                limit=50,
                offset=100,
            ),
        )

        # Derived-table subquery as source
        SelectParams(
            table=Subquery(
                params=SelectParams(table="events", fields=["user_id", "MAX(ts) AS last_ts"],
                                    filters=Filters(group_by="user_id")),
                alias="latest",
            ),
            fields=["user_id", "last_ts"],
        )
    """

    model_config = _ARBITRARY

    table: Union[str, Subquery]
    fields: Optional[List[Union[str, PostGISField]]] = None
    joins: Optional[List[Union[JoinClause, Dict[str, Any]]]] = None
    filters: Optional[Union[Filters, Dict[str, Any]]] = None

    @field_validator("fields", mode="before")
    @classmethod
    def _coerce_fields(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, (str, PostGISField)):
            return [v]
        return v

    @field_validator("joins", mode="before")
    @classmethod
    def _coerce_joins(cls, v: Any) -> Any:
        if v is None:
            return None
        if not isinstance(v, list):
            return [v]
        return v

    @field_validator("filters", mode="before")
    @classmethod
    def _coerce_filters(cls, v: Any) -> Any:
        if isinstance(v, dict):
            return Filters.model_validate(v)
        return v


# ---------------------------------------------------------------------------
# INSERT
# ---------------------------------------------------------------------------


class InsertParams(BaseModel):
    """
    Parameters for a single INSERT query.

    Example::

        InsertParams(
            table="users",
            values={"name": "Alice", "age": 30},
        )
        InsertParams(
            table="locations",
            values={"name": "HQ", "geom": PostGISValue("ST_MakePoint", [-5.9, 37.4])},
            on_conflict="name",
        )
    """

    model_config = _ARBITRARY

    table: str
    values: Dict[str, Any]
    on_conflict: Optional[List[str]] = None

    @field_validator("on_conflict", mode="before")
    @classmethod
    def _coerce_on_conflict(cls, v: Any) -> Any:
        if isinstance(v, str):
            return [v]
        return v


# ---------------------------------------------------------------------------
# UPDATE
# ---------------------------------------------------------------------------


class UpdateParams(BaseModel):
    """
    Parameters for a single UPDATE query.

    Example::

        UpdateParams(
            table="users",
            values={"name": "Bob"},
            filters=Filters(where=WhereCondition(field="id", operator="=", value=1)),
        )
    """

    model_config = _ARBITRARY

    table: str
    values: Dict[str, Any]
    filters: Optional[Union[Filters, Dict[str, Any]]] = None

    @field_validator("filters", mode="before")
    @classmethod
    def _coerce_filters(cls, v: Any) -> Any:
        if isinstance(v, dict):
            return Filters.model_validate(v)
        return v


# ---------------------------------------------------------------------------
# INSERT MANY
# ---------------------------------------------------------------------------


class InsertManyParams(BaseModel):
    """
    Configuration for a batch INSERT.

    The per-row data is passed separately as the ``records`` argument to
    :meth:`~.PostgreSQLInteractor.insert_many`.

    Example::

        InsertManyParams(table="users", on_conflict="email")
    """

    table: str
    on_conflict: Optional[List[str]] = None

    @field_validator("on_conflict", mode="before")
    @classmethod
    def _coerce_on_conflict(cls, v: Any) -> Any:
        if isinstance(v, str):
            return [v]
        return v


# ---------------------------------------------------------------------------
# UPDATE MANY
# ---------------------------------------------------------------------------


class UpdateManyParams(BaseModel):
    """
    Configuration for a batch UPDATE.

    ``value_keys`` are the columns written in SET; ``filter_keys`` are the
    columns used in WHERE to identify each row.  Both accept a single string
    as a shorthand.

    The per-row data is passed separately as the ``records`` argument to
    :meth:`~.PostgreSQLInteractor.update_many`.

    Example::

        UpdateManyParams(table="users", value_keys="status", filter_keys="id")
        UpdateManyParams(
            table="products",
            value_keys=["price", "stock"],
            filter_keys=["sku"],
        )
    """

    table: str
    value_keys: List[str]
    filter_keys: List[str]

    @field_validator("value_keys", "filter_keys", mode="before")
    @classmethod
    def _coerce_keys(cls, v: Any) -> Any:
        if isinstance(v, str):
            return [v]
        return v


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


class DeleteParams(BaseModel):
    """
    Parameters for a DELETE query.

    A WHERE clause is **mandatory** — unconditional deletes are rejected by the
    interactor to prevent accidental full-table wipes.

    Example::

        DeleteParams(
            table="sessions",
            filters=Filters(where=WhereCondition(field="expired", operator="=", value=True)),
        )
    """

    model_config = _ARBITRARY

    table: str
    filters: Union[Filters, Dict[str, Any]]

    @field_validator("filters", mode="before")
    @classmethod
    def _coerce_filters(cls, v: Any) -> Any:
        if isinstance(v, dict):
            return Filters.model_validate(v)
        return v


# ---------------------------------------------------------------------------
# Resolve forward references (Subquery <-> SelectParams circular dependency)
# ---------------------------------------------------------------------------

SelectParams.model_rebuild()
SubqueryCondition.model_rebuild()
ExistsCondition.model_rebuild()
Subquery.model_rebuild()
Filters.model_rebuild()
