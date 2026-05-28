"""Unit tests for query parameter schemas.

These tests exercise coercions and validation rules without requiring a live
database connection.
"""

import pytest
from pydantic import ValidationError as PydanticValidationError

from postgresql_interactor.postgis_types import PostGISField, PostGISKnnOrder
from postgresql_interactor.schemas import (
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


# ---------------------------------------------------------------------------
# SelectParams
# ---------------------------------------------------------------------------


class TestSelectParams:
    def test_fields_single_string_coerced(self):
        p = SelectParams(table="users", fields="name")
        assert p.fields == ["name"]

    def test_fields_list_unchanged(self):
        p = SelectParams(table="users", fields=["id", "name"])
        assert p.fields == ["id", "name"]

    def test_fields_none(self):
        p = SelectParams(table="users")
        assert p.fields is None

    def test_fields_postgis_object_coerced(self):
        pf = PostGISField("ST_AsGeoJSON", ["geom"])
        p = SelectParams(table="locations", fields=pf)
        assert p.fields == [pf]

    def test_joins_single_dict_coerced(self):
        p = SelectParams(
            table="users",
            joins={"type": "LEFT", "table": "orders", "on": "u.id = o.uid"},
        )
        assert isinstance(p.joins, list)
        assert len(p.joins) == 1

    def test_joins_list_unchanged(self):
        joins = [
            {"type": "LEFT", "table": "orders", "on": "u.id = o.uid"},
            {"type": "INNER", "table": "roles", "on": "u.rid = r.id"},
        ]
        p = SelectParams(table="users", joins=joins)
        assert len(p.joins) == 2

    def test_joins_none(self):
        p = SelectParams(table="users")
        assert p.joins is None

    def test_filters_dict_coerced_to_filters(self):
        p = SelectParams(table="users", filters={"limit": 10})
        assert isinstance(p.filters, Filters)
        assert p.filters.limit == 10

    def test_from_dict(self):
        p = SelectParams.model_validate({"table": "users", "fields": "email", "filters": {"limit": 5}})
        assert p.fields == ["email"]
        assert p.filters.limit == 5

    def test_subquery_as_table(self):
        inner = SelectParams(table="events", fields=["user_id"])
        sub = Subquery(params=inner, alias="ev")
        outer = SelectParams(table=sub, fields=["user_id"])
        assert isinstance(outer.table, Subquery)
        assert outer.table.alias == "ev"


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


class TestFilters:
    def test_where_single_dict_coerced(self):
        f = Filters(where={"field": "age", "operator": ">", "value": 18})
        assert isinstance(f.where, list)
        assert len(f.where) == 1

    def test_where_condition_object_coerced(self):
        cond = WhereCondition(field="id", operator="=", value=1)
        f = Filters(where=cond)
        assert len(f.where) == 1

    def test_where_list_unchanged(self):
        f = Filters(where=[
            {"field": "a", "operator": "=", "value": 1},
            {"field": "b", "operator": "!=", "value": 2},
        ])
        assert len(f.where) == 2

    def test_where_none(self):
        f = Filters()
        assert f.where is None

    def test_group_by_string_coerced(self):
        f = Filters(group_by="status")
        assert f.group_by == ["status"]

    def test_group_by_list_unchanged(self):
        f = Filters(group_by=["status", "region"])
        assert f.group_by == ["status", "region"]

    def test_order_by_single_coerced(self):
        f = Filters(order_by={"field": "name", "direction": "DESC"})
        assert isinstance(f.order_by, list)
        assert len(f.order_by) == 1

    def test_limit_must_be_positive(self):
        with pytest.raises(PydanticValidationError):
            Filters(limit=0)

    def test_limit_negative_rejected(self):
        with pytest.raises(PydanticValidationError):
            Filters(limit=-5)

    def test_limit_positive_ok(self):
        f = Filters(limit=1)
        assert f.limit == 1

    def test_offset_negative_rejected(self):
        with pytest.raises(PydanticValidationError):
            Filters(offset=-1)

    def test_offset_zero_ok(self):
        f = Filters(offset=0)
        assert f.offset == 0

    def test_offset_positive_ok(self):
        f = Filters(offset=100)
        assert f.offset == 100


# ---------------------------------------------------------------------------
# InsertParams
# ---------------------------------------------------------------------------


class TestInsertParams:
    def test_on_conflict_string_coerced(self):
        p = InsertParams(table="t", values={"x": 1}, on_conflict="email")
        assert p.on_conflict == ["email"]

    def test_on_conflict_list_unchanged(self):
        p = InsertParams(table="t", values={"x": 1}, on_conflict=["a", "b"])
        assert p.on_conflict == ["a", "b"]

    def test_on_conflict_none(self):
        p = InsertParams(table="t", values={"x": 1})
        assert p.on_conflict is None

    def test_from_dict(self):
        p = InsertParams.model_validate({"table": "t", "values": {"k": "v"}})
        assert p.table == "t"
        assert p.values == {"k": "v"}


# ---------------------------------------------------------------------------
# UpdateParams
# ---------------------------------------------------------------------------


class TestUpdateParams:
    def test_filters_dict_coerced(self):
        p = UpdateParams(
            table="t",
            values={"x": 1},
            filters={"where": {"field": "id", "operator": "=", "value": 1}},
        )
        assert isinstance(p.filters, Filters)
        assert len(p.filters.where) == 1

    def test_no_filters(self):
        p = UpdateParams(table="t", values={"x": 1})
        assert p.filters is None


# ---------------------------------------------------------------------------
# InsertManyParams
# ---------------------------------------------------------------------------


class TestInsertManyParams:
    def test_on_conflict_string_coerced(self):
        p = InsertManyParams(table="t", on_conflict="sku")
        assert p.on_conflict == ["sku"]

    def test_no_on_conflict(self):
        p = InsertManyParams(table="t")
        assert p.on_conflict is None


# ---------------------------------------------------------------------------
# UpdateManyParams
# ---------------------------------------------------------------------------


class TestUpdateManyParams:
    def test_single_strings_coerced(self):
        p = UpdateManyParams(table="t", value_keys="status", filter_keys="id")
        assert p.value_keys == ["status"]
        assert p.filter_keys == ["id"]

    def test_lists_unchanged(self):
        p = UpdateManyParams(table="t", value_keys=["a", "b"], filter_keys=["id"])
        assert p.value_keys == ["a", "b"]
        assert p.filter_keys == ["id"]


# ---------------------------------------------------------------------------
# DeleteParams
# ---------------------------------------------------------------------------


class TestDeleteParams:
    def test_filters_dict_coerced(self):
        p = DeleteParams(
            table="t",
            filters={"where": {"field": "expired", "operator": "=", "value": True}},
        )
        assert isinstance(p.filters, Filters)

    def test_from_dict(self):
        p = DeleteParams.model_validate({
            "table": "sessions",
            "filters": {"where": [{"field": "id", "operator": "=", "value": 99}]},
        })
        assert p.table == "sessions"
        assert len(p.filters.where) == 1


# ---------------------------------------------------------------------------
# OrderByClause
# ---------------------------------------------------------------------------


class TestOrderByClause:
    def test_direction_uppercased(self):
        ob = OrderByClause(field="name", direction="asc")
        assert ob.direction == "ASC"

    def test_default_direction_asc(self):
        ob = OrderByClause(field="name")
        assert ob.direction == "ASC"

    def test_requires_at_least_one_of_field_postgis_knn(self):
        with pytest.raises(PydanticValidationError):
            OrderByClause(direction="ASC")

    def test_field_ok(self):
        ob = OrderByClause(field="created_at", direction="DESC")
        assert ob.field == "created_at"
        assert ob.direction == "DESC"

    def test_postgis_ok(self):
        pf = PostGISField("ST_Area", ["geom"])
        ob = OrderByClause(postgis=pf, direction="ASC")
        assert ob.postgis is pf

    def test_knn_ok(self):
        knn = PostGISKnnOrder("p.geom", "ST_MakePoint(%s,%s)", values=[1.0, 2.0])
        ob = OrderByClause(knn=knn)
        assert ob.knn is knn


# ---------------------------------------------------------------------------
# JoinClause
# ---------------------------------------------------------------------------


class TestJoinClause:
    def test_type_uppercased(self):
        j = JoinClause(type="left", table="orders", on="u.id = o.uid")
        assert j.type == "LEFT"

    def test_default_type_inner(self):
        j = JoinClause(table="orders", on="u.id = o.uid")
        assert j.type == "INNER"


# ---------------------------------------------------------------------------
# Subquery and subquery conditions
# ---------------------------------------------------------------------------


class TestSubquery:
    def test_basic(self):
        inner = SelectParams(table="orders", fields=["user_id"])
        sub = Subquery(params=inner, alias="o")
        assert sub.alias == "o"
        assert sub.params.table == "orders"

    def test_nested_subquery(self):
        level1 = SelectParams(table="events", fields=["user_id"])
        sub1 = Subquery(params=level1, alias="ev")
        level2 = SelectParams(table=sub1, fields=["user_id"])
        sub2 = Subquery(params=level2, alias="outer")
        assert isinstance(sub2.params.table, Subquery)


class TestSubqueryCondition:
    def test_basic(self):
        inner = SelectParams(table="orders", fields=["user_id"])
        sub = Subquery(params=inner, alias="o")
        cond = SubqueryCondition(field="id", operator="IN", subquery=sub)
        assert cond.field == "id"
        assert cond.operator == "IN"


class TestExistsCondition:
    def test_basic(self):
        inner = SelectParams(table="orders")
        sub = Subquery(params=inner, alias="o")
        cond = ExistsCondition(subquery=sub)
        assert cond.negate is False

    def test_negated(self):
        inner = SelectParams(table="orders")
        sub = Subquery(params=inner, alias="o")
        cond = ExistsCondition(subquery=sub, negate=True)
        assert cond.negate is True


# ---------------------------------------------------------------------------
# PostGIS types (dataclasses)
# ---------------------------------------------------------------------------


class TestPostGISTypes:
    def test_postgis_field_defaults(self):
        pf = PostGISField("ST_AsGeoJSON", ["geom"])
        assert pf.alias is None
        assert pf.values == []

    def test_postgis_field_values_none_coerced(self):
        pf = PostGISField("ST_AsGeoJSON", ["geom"], values=None)
        assert pf.values == []

    def test_postgis_condition_defaults(self):
        pc = PostGISField("ST_Within", ["geom", "%s"])
        assert pc.values == []

    def test_postgis_knn_values_none_coerced(self):
        from postgresql_interactor.postgis_types import PostGISKnnOrder
        knn = PostGISKnnOrder("a.geom", "b.geom", values=None)
        assert knn.values == []
