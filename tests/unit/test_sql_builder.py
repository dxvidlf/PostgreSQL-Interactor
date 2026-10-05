"""Unit tests for the SQL the interactor builds from SELECT parameters.

No database connection: the interactor is created without running __init__ and
given fixed table/column allow-lists, and the private SQL builder is called
directly.
"""

import pytest

from postgresql_interactor import (
    Aggregate,
    Filters,
    OrderByClause,
    PostgreSQLInteractor,
    PostGISField,
    SelectParams,
    Subquery,
    SubqueryCondition,
    ValidationError,
    WhereCondition,
    WindowFunction,
)
from postgresql_interactor.exceptions import PostGISError


@pytest.fixture
def db():
    inst = PostgreSQLInteractor.__new__(PostgreSQLInteractor)
    inst._PostgreSQLInteractor__allowed_tables = {"events", "users", "locations"}
    inst._PostgreSQLInteractor__allowed_fields = {
        "id", "user_id", "ts", "kind", "payload", "name", "geom",
    }
    return inst


def build(db, params):
    values: list = []
    sql = db._PostgreSQLInteractor__build_select_sql(params, values)
    return sql, values


# ---------------------------------------------------------------------------
# Aggregates
# ---------------------------------------------------------------------------


class TestAggregates:
    def test_count_min_max(self, db):
        sql, values = build(db, SelectParams(
            table="events",
            fields=[
                Aggregate(function="COUNT", alias="n"),
                Aggregate(function="MIN", field="ts", alias="first_ts"),
                Aggregate(function="MAX", field="ts", alias="last_ts"),
            ],
            filters=Filters(where=WhereCondition(field="user_id", operator="=", value=7)),
        ))
        assert sql == (
            "SELECT COUNT(*) AS n, MIN(ts) AS first_ts, MAX(ts) AS last_ts"
            " FROM events WHERE user_id = %s"
        )
        assert values == [7]

    def test_group_by_and_order_by_own_alias(self, db):
        sql, _ = build(db, SelectParams(
            table="events",
            fields=["user_id", Aggregate(function="COUNT", field="kind", distinct=True, alias="kinds")],
            filters=Filters(group_by="user_id", order_by=OrderByClause(field="kinds", direction="DESC")),
        ))
        assert sql == (
            "SELECT user_id, COUNT(DISTINCT kind) AS kinds FROM events"
            " GROUP BY user_id ORDER BY kinds DESC"
        )

    def test_without_alias(self, db):
        sql, _ = build(db, SelectParams(table="events", fields=Aggregate(function="sum", field="e.id")))
        assert sql == "SELECT SUM(e.id) FROM events"

    def test_unknown_column_rejected(self, db):
        with pytest.raises(ValidationError):
            build(db, SelectParams(table="events", fields=Aggregate(function="MAX", field="password")))

    def test_own_alias_not_valid_in_where(self, db):
        with pytest.raises(ValidationError):
            build(db, SelectParams(
                table="events",
                fields=Aggregate(function="COUNT", alias="n"),
                filters=Filters(where=WhereCondition(field="n", operator=">", value=1)),
            ))

    def test_unsafe_alias_rejected(self, db):
        with pytest.raises(PostGISError):
            build(db, SelectParams(table="events", fields=Aggregate(function="COUNT", alias="n; DROP")))

    def test_function_revalidated_when_schema_skipped(self, db):
        bad = Aggregate.model_construct(function="PG_SLEEP", field="id", distinct=False, alias=None)
        with pytest.raises(ValidationError):
            build(db, SelectParams.model_construct(table="events", fields=[bad], joins=None, filters=None))


# ---------------------------------------------------------------------------
# Window functions
# ---------------------------------------------------------------------------


class TestWindowFunctions:
    def test_row_number(self, db):
        sql, _ = build(db, SelectParams(
            table="events",
            fields=["id", WindowFunction(
                function="ROW_NUMBER",
                order_by=[OrderByClause(field="ts"), OrderByClause(field="id")],
                alias="rn",
            )],
        ))
        assert sql == "SELECT id, ROW_NUMBER() OVER (ORDER BY ts ASC, id ASC) AS rn FROM events"

    def test_partition_and_ntile(self, db):
        sql, _ = build(db, SelectParams(
            table="events e",
            fields=WindowFunction(
                function="NTILE", buckets=4, partition_by="e.user_id",
                order_by={"field": "e.ts", "direction": "DESC"}, alias="q",
            ),
        ))
        assert sql == (
            "SELECT NTILE(4) OVER (PARTITION BY e.user_id ORDER BY e.ts DESC) AS q FROM events e"
        )

    def test_aggregate_over_partition(self, db):
        sql, _ = build(db, SelectParams(
            table="events",
            fields=[
                WindowFunction(function="COUNT", partition_by="user_id", alias="per_user"),
                WindowFunction(function="MAX", field="ts", alias="last_ts"),
            ],
        ))
        assert sql == (
            "SELECT COUNT(*) OVER (PARTITION BY user_id) AS per_user,"
            " MAX(ts) OVER () AS last_ts FROM events"
        )

    def test_unknown_partition_column_rejected(self, db):
        with pytest.raises(ValidationError):
            build(db, SelectParams(
                table="events",
                fields=WindowFunction(function="RANK", partition_by="secret", order_by={"field": "ts"}),
            ))

    def test_unknown_order_column_rejected(self, db):
        with pytest.raises(ValidationError):
            build(db, SelectParams(
                table="events",
                fields=WindowFunction(function="ROW_NUMBER", order_by={"field": "ts; DROP TABLE events"}),
            ))

    def test_function_revalidated_when_schema_skipped(self, db):
        bad = WindowFunction.model_construct(
            function="LAG", field="ts", buckets=None, partition_by=None, order_by=None, alias=None,
        )
        with pytest.raises(ValidationError):
            build(db, SelectParams.model_construct(table="events", fields=[bad], joins=None, filters=None))

    def test_ntile_buckets_revalidated(self, db):
        bad = WindowFunction.model_construct(
            function="NTILE", field=None, buckets="4) OVER (); --", partition_by=None, order_by=None, alias=None,
        )
        with pytest.raises(ValidationError):
            build(db, SelectParams.model_construct(table="events", fields=[bad], joins=None, filters=None))


# ---------------------------------------------------------------------------
# Derived tables: their columns are valid in the outer query
# ---------------------------------------------------------------------------


def numbered(**where):
    return Subquery(
        params=SelectParams(
            table="events",
            fields=["id", "ts", "payload", WindowFunction(
                function="ROW_NUMBER", order_by=[{"field": "ts"}, {"field": "id"}], alias="rn",
            )],
            filters=Filters(where=WhereCondition(**where)) if where else None,
        ),
        alias="s",
    )


class TestDerivedColumns:
    def test_filter_by_window_alias(self, db):
        sql, values = build(db, SelectParams(
            table=numbered(field="user_id", operator="=", value=3),
            fields=["id", "ts", "payload"],
            filters=Filters(
                where=WhereCondition(field="rn", operator="IN", value=[1, 5, 9]),
                order_by=OrderByClause(field="rn"),
            ),
        ))
        assert sql == (
            "SELECT id, ts, payload FROM (SELECT id, ts, payload,"
            " ROW_NUMBER() OVER (ORDER BY ts ASC, id ASC) AS rn FROM events WHERE user_id = %s) AS s"
            " WHERE rn IN (%s, %s, %s) ORDER BY rn ASC"
        )
        assert values == [3, 1, 5, 9]

    def test_qualified_and_selected(self, db):
        sql, _ = build(db, SelectParams(table=numbered(), fields=["s.id", "s.rn"]))
        assert sql.startswith("SELECT s.id, s.rn FROM (")

    def test_aggregate_alias_in_outer_where(self, db):
        inner = SelectParams(
            table="events",
            fields=["user_id", Aggregate(function="COUNT", alias="order_count")],
            filters=Filters(group_by="user_id"),
        )
        sql, values = build(db, SelectParams(
            table=Subquery(params=inner, alias="stats"),
            fields=["user_id", "order_count"],
            filters=Filters(where=WhereCondition(field="order_count", operator=">", value=5)),
        ))
        assert sql == (
            "SELECT user_id, order_count FROM (SELECT user_id, COUNT(*) AS order_count"
            " FROM events GROUP BY user_id) AS stats WHERE order_count > %s"
        )
        assert values == [5]

    def test_columns_pass_through_select_star(self, db):
        outer = Subquery(params=SelectParams(table=numbered()), alias="t")
        sql, _ = build(db, SelectParams(
            table=outer, fields="rn",
            filters=Filters(where=WhereCondition(field="rn", operator="<", value=3)),
        ))
        assert sql.endswith(") AS s) AS t WHERE rn < %s")

    def test_derived_alias_does_not_leak_to_other_queries(self, db):
        build(db, SelectParams(table=numbered(), fields="rn"))
        with pytest.raises(ValidationError):
            build(db, SelectParams(table="events", fields="rn"))

    def test_derived_alias_not_valid_inside_the_subquery_condition(self, db):
        cond_sub = Subquery(params=SelectParams(table="users", fields="rn"), alias="u")
        with pytest.raises(ValidationError):
            build(db, SelectParams(
                table=numbered(),
                fields="id",
                filters=Filters(where=SubqueryCondition(field="rn", operator="IN", subquery=cond_sub)),
            ))

    def test_unknown_column_still_rejected(self, db):
        with pytest.raises(ValidationError):
            build(db, SelectParams(table=numbered(), fields="secret"))


# ---------------------------------------------------------------------------
# Parameter order
# ---------------------------------------------------------------------------


class TestParameterOrder:
    def test_select_list_values_precede_derived_table_values(self, db):
        """The SELECT list comes before FROM in the SQL text, so its parameters
        must be bound first even though FROM is built first."""
        inner = SelectParams(
            table="locations",
            fields=["id", "geom"],
            filters=Filters(where=WhereCondition(field="user_id", operator="=", value=42)),
        )
        sql, values = build(db, SelectParams(
            table=Subquery(params=inner, alias="l"),
            fields=["id", PostGISField("ST_Buffer", ["geom", "%s"], alias="b", values=[10])],
            filters=Filters(where=WhereCondition(field="id", operator="<=", value=100)),
        ))
        assert sql == (
            "SELECT id, ST_Buffer(geom, %s) AS b FROM (SELECT id, geom FROM locations"
            " WHERE user_id = %s) AS l WHERE id <= %s"
        )
        assert values == [10, 42, 100]
