# PostgreSQL Interactor

A secure, typed PostgreSQL wrapper with full **PostGIS** support.  
Prevents SQL injection through strict allow-lists of tables, columns, operators,
and PostGIS functions validated directly against the live database schema.

## Features

- **Full CRUD**: `select`, `insert`, `insert_many`, `update`, `update_many`, `delete`
- **Pydantic v2 parameter schemas** with automatic input coercions
- **Nested / subquery support**: derived tables, `IN (SELECT …)`, `[NOT] EXISTS`
- **JOINs**: INNER, LEFT, RIGHT, FULL, CROSS — ON clause fully validated
- **GROUP BY / ORDER BY / LIMIT / OFFSET**
- **Complete PostGIS support**:
  - Output: `ST_AsGeoJSON`, `ST_AsText`, `ST_AsEWKT`, …
  - Metrics: `ST_Area`, `ST_Distance`, `ST_Length`, …
  - Spatial predicates: `ST_Within`, `ST_Intersects`, `ST_DWithin`, …
  - Constructors: `ST_GeomFromText`, `ST_MakePoint`, `ST_MakeEnvelope`, …
  - KNN ordering with the `<->` operator
- **Fully parameterized**: all values are passed as `%s` — never string-interpolated
- **Schema-aware**: only tables and columns that actually exist in the database are allowed
- **Flexible input**: single values are promoted to lists automatically
- **Typed**: ships with a `py.typed` marker (PEP 561) — fully compatible with mypy and pyright

---

## Installation

### From GitHub

```bash
pip install git+https://github.com/dxvidlf/PostgreSQL-Interactor.git
```

With `.env` file support (requires `pydantic-settings`):

```bash
pip install "postgresql-interactor[pydantic] @ git+https://github.com/dxvidlf/PostgreSQL-Interactor.git"
```

### Local development

```bash
git clone https://github.com/dxvidlf/PostgreSQL-Interactor.git
cd PostgreSQL-Interactor
pip install -e ".[dev]"   # editable install with test dependencies
```

---

## Quick start

```python
from postgresql_interactor import PostgreSQLInteractor

db = PostgreSQLInteractor(
    db_name="my_db",
    ip="localhost",
    port=5432,
    username="user",
    password="secret",
)

rows = db.select({"table": "users", "filters": {"limit": 10}})
```

---

## Connection

### Direct parameters

```python
from postgresql_interactor import PostgreSQLInteractor

db = PostgreSQLInteractor(
    db_name="my_db",
    ip="localhost",
    port=5432,
    username="user",
    password="secret",
)
```

### Via `.env` file

Requires `pip install postgresql-interactor[pydantic]`.

```python
db = PostgreSQLInteractor()  # reads from .env automatically
```

`.env` (extra variables like `DEBUG` or `API_KEY` are ignored):

```env
DB_NAME=my_db
DB_IP=localhost
DB_PORT=5432
DB_USERNAME=user
DB_PASSWORD=secret
```

### Reload schema after migrations

If you run migrations and add new tables or columns, call `reload_schema()` to
refresh the allow-lists without re-instantiating:

```python
db.reload_schema()
```

---

## Parameter schemas

Every method accepts either a **typed schema object** or a plain **dict**.
Both styles are equivalent — dicts are validated and coerced automatically.

```python
from postgresql_interactor import SelectParams, Filters, WhereCondition

# typed
rows = db.select(SelectParams(
    table="users",
    fields=["id", "name"],
    filters=Filters(limit=10),
))

# dict (identical behaviour)
rows = db.select({
    "table": "users",
    "fields": ["id", "name"],
    "filters": {"limit": 10},
})
```

### Automatic coercions

Anywhere a list is expected, you can pass a single item:

```python
# All of these are equivalent
SelectParams(table="users", fields="name")
SelectParams(table="users", fields=["name"])

Filters(group_by="status")
Filters(group_by=["status"])

UpdateManyParams(table="t", value_keys="price", filter_keys="id")
```

---

## SELECT

```python
from postgresql_interactor import (
    PostgreSQLInteractor,
    SelectParams, Filters, WhereCondition, JoinClause, OrderByClause,
    PostGISField, PostGISCondition,
)

rows = db.select(SelectParams(
    table="locations AS loc",
    fields=[
        "loc.id",
        "loc.name",
        PostGISField("ST_AsGeoJSON", ["loc.geom"], alias="geojson"),
        PostGISField("ST_Distance",  ["loc.geom", "ref.geom"], alias="dist"),
    ],
    joins=JoinClause(type="INNER", table="ref_points AS ref", on="loc.city_id = ref.id"),
    filters=Filters(
        where=[
            WhereCondition(field="loc.active", operator="=", value=True),
            PostGISCondition(
                "ST_DWithin",
                ["loc.geom", "ST_MakePoint(%s,%s)::geography", "%s"],
                values=[-5.9, 37.4, 1000],
            ),
        ],
        group_by=["loc.id", "loc.name"],
        order_by=OrderByClause(
            postgis=PostGISField("ST_Distance", ["loc.geom", "ref.geom"]),
            direction="ASC",
        ),
        limit=20,
        offset=40,
    ),
))
```

Or with a plain dict (same result):

```python
rows = db.select({
    "table": "locations AS loc",
    "fields": ["loc.id", "loc.name"],
    "filters": {
        "where": {"field": "loc.active", "operator": "=", "value": True},
        "order_by": {"field": "loc.name", "direction": "ASC"},
        "limit": 20,
        "offset": 40,
    },
})
```

### Supported WHERE operators

`=` `!=` `<` `<=` `>` `>=` `IN` `NOT IN` `BETWEEN` `IS NULL` `IS NOT NULL` `LIKE` `ILIKE`

```python
# Multi-value operators
WhereCondition(field="status", operator="IN",      value=["active", "pending"])
WhereCondition(field="age",    operator="BETWEEN", value=[18, 65])
WhereCondition(field="email",  operator="IS NOT NULL")
WhereCondition(field="name",   operator="ILIKE",   value="%alice%")
```

---

## Nested queries (subqueries)

### Derived table in FROM

```python
from postgresql_interactor import Subquery, SelectParams, Filters

inner = SelectParams(
    table="orders",
    fields=["user_id", "COUNT(*) AS order_count"],
    filters=Filters(group_by="user_id"),
)

rows = db.select(SelectParams(
    table=Subquery(params=inner, alias="stats"),
    fields=["user_id", "order_count"],
    filters=Filters(
        where=WhereCondition(field="order_count", operator=">", value=5)
    ),
))
# → SELECT user_id, order_count
#   FROM (SELECT user_id, COUNT(*) AS order_count FROM orders GROUP BY user_id) AS stats
#   WHERE order_count > %s
```

### Subquery in WHERE (`IN`, `=`, …)

```python
from postgresql_interactor import SubqueryCondition, Subquery

active_depts = Subquery(
    params=SelectParams(
        table="departments",
        fields="id",
        filters=Filters(where=WhereCondition(field="active", operator="=", value=True)),
    ),
    alias="d",
)

rows = db.select(SelectParams(
    table="employees",
    filters=Filters(
        where=SubqueryCondition(field="dept_id", operator="IN", subquery=active_depts)
    ),
))
# → SELECT * FROM employees WHERE dept_id IN (SELECT id FROM departments WHERE active = %s)
```

### EXISTS / NOT EXISTS

```python
from postgresql_interactor import ExistsCondition, Subquery

has_orders = Subquery(
    params=SelectParams(
        table="orders",
        filters=Filters(where=WhereCondition(field="user_id", operator="=", value=42)),
    ),
    alias="o",
)

rows = db.select(SelectParams(
    table="users",
    filters=Filters(where=ExistsCondition(subquery=has_orders)),
))
# → SELECT * FROM users WHERE EXISTS (SELECT * FROM orders WHERE user_id = %s)
```

---

## INSERT

```python
from postgresql_interactor import InsertParams, PostGISValue

ok, n = db.insert(InsertParams(
    table="locations",
    values={
        "name": "Headquarters",
        "geom": PostGISValue("ST_GeomFromText", ["POINT(-5.9 37.4)", 4326]),
    },
    on_conflict="name",   # ON CONFLICT (name) DO NOTHING
))
```

`dict` values are serialised to JSON automatically:

```python
ok, n = db.insert({
    "table": "events",
    "values": {"name": "launch", "metadata": {"env": "prod", "version": 3}},
})
```

---

## Bulk INSERT

```python
from postgresql_interactor import InsertManyParams, PostGISValue

ok, total = db.insert_many(
    InsertManyParams(table="locations", on_conflict="name"),
    records=[
        {"name": "Point A", "geom": PostGISValue("ST_MakePoint", [-5.9, 37.4])},
        {"name": "Point B", "geom": PostGISValue("ST_MakePoint", [-5.8, 37.5])},
    ],
)
```

---

## UPDATE

```python
from postgresql_interactor import UpdateParams, Filters, WhereCondition, PostGISValue

ok, n = db.update(UpdateParams(
    table="locations",
    values={"geom": PostGISValue("ST_GeomFromText", ["POINT(-5.8 37.5)", 4326])},
    filters=Filters(where=WhereCondition(field="id", operator="=", value=42)),
))
```

---

## Bulk UPDATE

```python
from postgresql_interactor import UpdateManyParams, PostGISValue

ok, total = db.update_many(
    UpdateManyParams(table="locations", value_keys="geom", filter_keys="id"),
    records=[
        {"id": 1, "geom": PostGISValue("ST_GeomFromText", ["POINT(-5.9 37.4)", 4326])},
        {"id": 2, "geom": PostGISValue("ST_GeomFromText", ["POINT(-5.8 37.5)", 4326])},
    ],
)
```

---

## DELETE

```python
from postgresql_interactor import DeleteParams, Filters, PostGISCondition

ok, n = db.delete(DeleteParams(
    table="locations",
    filters=Filters(
        where=PostGISCondition(
            "ST_Within",
            ["geom", "ST_MakeEnvelope(%s,%s,%s,%s,4326)"],
            values=[-6.0, 37.3, -5.8, 37.5],
        )
    ),
))
```

> DELETE without a WHERE clause is forbidden and raises `ValidationError` immediately.

---

## KNN ordering

```python
from postgresql_interactor import PostGISKnnOrder, OrderByClause

rows = db.select({
    "table": "points AS p",
    "fields": ["p.id", "p.name"],
    "filters": {
        "order_by": OrderByClause(
            knn=PostGISKnnOrder(
                "p.geom",
                "ST_Transform(ST_SetSRID(ST_MakePoint(%s,%s), %s), %s)",
                values=[-5.9, 37.4, 4326, 25830],
            ),
            direction="ASC",
        ),
        "limit": 10,
    },
})
```

---

## Error handling

```python
from postgresql_interactor import (
    QueryExecutionError,
    ValidationError,
    PostGISError,
)

try:
    ok, n = db.insert({"table": "users", "values": {"name": "Alice"}})
except ValidationError as e:
    # Bad table name, unknown column, forbidden operator, …
    print(f"Validation error: {e}")
except PostGISError as e:
    # Forbidden or unrecognised PostGIS function
    print(f"PostGIS error: {e}")
except QueryExecutionError as e:
    # Database returned an error (constraint violation, type mismatch, …)
    print(f"Database error: {e}")
```

### Exception reference

| Exception | Description |
| --- | --- |
| `PostgreSQLInteractorError` | Base class for all exceptions |
| `ValidationError` | Invalid table, column, operator, or alias |
| `ConfigurationError` | Missing or invalid database configuration |
| `QueryExecutionError` | Database-level execution failure |
| `PostGISError` | Forbidden or unrecognised PostGIS function |

---

## Security model

| Protection | Mechanism |
| --- | --- |
| Table injection | Allow-list from `information_schema.tables` |
| Column injection | Allow-list from `information_schema.columns` |
| Operator injection | Fixed set of allowed SQL operators |
| PostGIS injection | Three separate function whitelists (SELECT, WHERE, INSERT/UPDATE) |
| Value injection | All values passed as `%s` parameters — never interpolated |
| Alias injection | Regex `[A-Za-z0-9_]+` enforced on every alias |
| Accidental full-delete | DELETE without WHERE raises `ValidationError` before opening a connection |

---

## Project structure

```text
PostgreSQL-Interactor/
├── src/
│   └── postgresql_interactor/
│       ├── __init__.py           # Public API and exports
│       ├── interactor.py         # PostgreSQLInteractor class
│       ├── schemas.py            # Pydantic v2 parameter schemas
│       ├── postgis_types.py      # PostGISField, PostGISCondition, PostGISValue, PostGISKnnOrder
│       ├── postgis_registry.py   # PostGIS function whitelists and validators
│       ├── exceptions.py         # Custom exception hierarchy
│       ├── config.py             # .env configuration (optional, requires pydantic-settings)
│       └── py.typed              # PEP 561 typed package marker
├── tests/
│   └── unit/
│       └── test_schemas.py       # 52 schema / coercion unit tests (no DB needed)
├── pyproject.toml
├── CHANGELOG.md
├── LICENSE
└── README.md
```

---

## License

MIT
