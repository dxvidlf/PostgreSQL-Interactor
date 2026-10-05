"""Integration tests: aggregates, window functions and derived tables against a
real PostgreSQL.

They run only when ``TEST_PG_HOST`` is set (otherwise they are skipped), e.g.::

    docker run -d --name pgi-test -e POSTGRES_PASSWORD=test -p 55433:5432 postgres:16
    TEST_PG_HOST=localhost TEST_PG_PORT=55433 TEST_PG_PASSWORD=test pytest tests/integration

Other variables (defaults): ``TEST_PG_USER=postgres``, ``TEST_PG_DB=postgres``.
The tests create the table ``pgi_it_events`` in the ``public`` schema and drop it
at the end: point them at a throwaway database, never at a real one.
"""

import os
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from postgresql_interactor import (
    Aggregate,
    Filters,
    OrderByClause,
    PostgreSQLInteractor,
    SelectParams,
    Subquery,
    WhereCondition,
    WindowFunction,
)

if not os.environ.get("TEST_PG_HOST"):
    pytest.skip("TEST_PG_HOST not set: no test database", allow_module_level=True)

CONN = dict(
    host=os.environ["TEST_PG_HOST"],
    port=int(os.environ.get("TEST_PG_PORT", "5432")),
    user=os.environ.get("TEST_PG_USER", "postgres"),
    password=os.environ.get("TEST_PG_PASSWORD", ""),
    dbname=os.environ.get("TEST_PG_DB", "postgres"),
)
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
N = 25   # rows; user 1 has the even ids, user 2 the odd ones


@pytest.fixture(scope="module")
def db():
    with psycopg.connect(**CONN, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS pgi_it_events")
        conn.execute(
            "CREATE TABLE pgi_it_events (id INT PRIMARY KEY, user_id INT, ts TIMESTAMPTZ, kind TEXT, score INT)"
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO pgi_it_events VALUES (%s, %s, %s, %s, %s)",
                [(i, 1 if i % 2 == 0 else 2, T0 + timedelta(hours=i), "ab"[i % 3 == 0], i * 10)
                 for i in range(1, N + 1)],
            )
    try:
        yield PostgreSQLInteractor(
            db_name=CONN["dbname"], ip=CONN["host"], port=CONN["port"],
            username=CONN["user"], password=CONN["password"],
        )
    finally:
        with psycopg.connect(**CONN, autocommit=True) as conn:
            conn.execute("DROP TABLE IF EXISTS pgi_it_events")


def test_count_min_max(db):
    [row] = db.select(SelectParams(
        table="pgi_it_events",
        fields=[
            Aggregate(function="COUNT", alias="n"),
            Aggregate(function="MIN", field="ts", alias="first_ts"),
            Aggregate(function="MAX", field="ts", alias="last_ts"),
        ],
        filters=Filters(where=WhereCondition(field="user_id", operator="=", value=1)),
    ))
    assert row["n"] == N // 2
    assert row["first_ts"] == T0 + timedelta(hours=2)
    assert row["last_ts"] == T0 + timedelta(hours=24)


def test_count_on_empty_range(db):
    [row] = db.select(SelectParams(
        table="pgi_it_events",
        fields=[Aggregate(function="COUNT", alias="n"), Aggregate(function="MAX", field="ts", alias="last_ts")],
        filters=Filters(where=WhereCondition(field="user_id", operator="=", value=99)),
    ))
    assert row == {"n": 0, "last_ts": None}


def test_group_by_ordered_by_aggregate_alias(db):
    rows = db.select(SelectParams(
        table="pgi_it_events",
        fields=["kind", Aggregate(function="SUM", field="score", alias="total")],
        filters=Filters(group_by="kind", order_by=OrderByClause(field="total", direction="DESC")),
    ))
    assert [r["kind"] for r in rows] == ["a", "b"]
    assert sum(r["total"] for r in rows) == sum(i * 10 for i in range(1, N + 1))


def test_rows_at_positions_through_row_number(db):
    """The pattern that motivated window functions: pick the rows at given
    positions of an ordered range without fetching the rest."""
    inner = SelectParams(
        table="pgi_it_events",
        fields=["id", "ts", WindowFunction(
            function="ROW_NUMBER", order_by=[{"field": "ts"}, {"field": "id"}], alias="rn",
        )],
        filters=Filters(where=WhereCondition(field="user_id", operator="=", value=2)),
    )
    rows = db.select(SelectParams(
        table=Subquery(params=inner, alias="s"),
        fields=["id", "rn"],
        filters=Filters(
            where=WhereCondition(field="rn", operator="IN", value=[1, 4, 13]),
            order_by=OrderByClause(field="rn"),
        ),
    ))
    assert [(r["rn"], r["id"]) for r in rows] == [(1, 1), (4, 7), (13, 25)]


def test_ntile_and_partitioned_count(db):
    rows = db.select(SelectParams(
        table="pgi_it_events",
        fields=[
            "id",
            WindowFunction(function="NTILE", buckets=5, order_by={"field": "id"}, alias="bucket"),
            WindowFunction(function="COUNT", partition_by="user_id", alias="per_user"),
        ],
        filters=Filters(order_by=OrderByClause(field="id")),
    ))
    assert [r["bucket"] for r in rows] == [b for b in range(1, 6) for _ in range(5)]
    assert {r["per_user"] for r in rows if r["id"] % 2 == 0} == {N // 2}


def test_aggregate_alias_filtered_in_outer_query(db):
    inner = SelectParams(
        table="pgi_it_events",
        fields=["user_id", Aggregate(function="COUNT", alias="events")],
        filters=Filters(group_by="user_id"),
    )
    rows = db.select(SelectParams(
        table=Subquery(params=inner, alias="stats"),
        fields=["user_id", "events"],
        filters=Filters(where=WhereCondition(field="events", operator=">", value=12)),
    ))
    assert rows == [{"user_id": 2, "events": 13}]
