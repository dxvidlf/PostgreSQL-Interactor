# PostgreSQL Interactor

A secure, typed PostgreSQL wrapper with full **PostGIS** support.  
Designed to prevent SQL injection through strict whitelists of tables, fields,
operators, and PostGIS functions.

## Features

- **Full CRUD**: `select`, `insert`, `insert_many`, `update`, `update_many`, `delete`
- **JOINs**: INNER, LEFT, RIGHT, FULL, CROSS with ON clause validation
- **GROUP BY / ORDER BY / LIMIT**
- **Complete PostGIS support**:
  - Output functions: `ST_AsGeoJSON`, `ST_AsText`, `ST_AsEWKT`, etc.
  - Metrics: `ST_Area`, `ST_Distance`, `ST_Length`, etc.
  - Spatial predicates: `ST_Within`, `ST_Intersects`, `ST_DWithin`, etc.
  - Constructors: `ST_GeomFromText`, `ST_MakePoint`, `ST_MakeEnvelope`, etc.
  - KNN ordering with `<->` operator
- **Safe parameterization**: all values are passed as parameters (`%s`)
- **Whitelists**: only tables and columns that actually exist in the database are allowed
- **Flexible configuration**: via `.env` (with pydantic) or directly in the constructor

## Installation

### From GitHub (recommended while not on PyPI)

```bash
pip install git+https://github.com/dxvidlf/PostgreSQL-Interactor.git
```

With `.env` file support:

```bash
pip install "postgresql-interactor[pydantic] @ git+https://github.com/dxvidlf/PostgreSQL-Interactor.git"
```

### Local (development)

```bash
git clone https://github.com/dxvidlf/PostgreSQL-Interactor.git
cd PostgreSQL-Interactor
pip install -e .              # editable install
pip install -e ".[pydantic]"  # with .env support
```

## Basic usage

### Connection

```python
from postgresql_interactor import PostgreSQLInteractor

# Option A: direct parameters
pg = PostgreSQLInteractor(
    db_name="my_db",
    ip="localhost",
    port=5432,
    username="user",
    password="password",
)

# Option B: .env file (requires pip install postgresql-interactor[pydantic])
pg = PostgreSQLInteractor()
```

`.env` file:

```
DB_NAME=my_db
DB_IP=localhost
DB_PORT=5432
DB_USERNAME=user
DB_PASSWORD=password
```

### SELECT

```python
from postgresql_interactor import PostgreSQLInteractor, PostGISField, PostGISCondition

results = pg.select({
    "table": "locations AS loc",
    "fields": [
        "loc.id",
        "loc.name",
        PostGISField("ST_AsGeoJSON", ["loc.geom"], alias="geojson"),
        PostGISField("ST_Distance", ["loc.geom", "ref.geom"], alias="dist"),
    ],
    "joins": [
        {"type": "INNER", "table": "ref_points AS ref", "on": "loc.city_id = ref.id"},
    ],
    "filters": {
        "where": [
            {"field": "loc.active", "operator": "=", "value": True},
            PostGISCondition(
                "ST_DWithin",
                ["loc.geom", "ST_MakePoint(%s,%s)::geography", "%s"],
                values=[-5.9, 37.4, 1000],
            ),
        ],
        "group_by": ["loc.id", "loc.name"],
        "order_by": [
            {"postgis": PostGISField("ST_Distance", ["loc.geom", "ref.geom"]), "direction": "ASC"},
        ],
        "limit": 20,
    },
})
```

### INSERT

```python
from postgresql_interactor import PostgreSQLInteractor, PostGISValue

ok, rows = pg.insert({
    "table": "locations",
    "values": {
        "name": "Headquarters",
        "geom": PostGISValue("ST_GeomFromText", ["POINT(-5.9 37.4)", 4326]),
    },
    "on_conflict": ["name"],  # ON CONFLICT (name) DO NOTHING
})
```

### Bulk INSERT

```python
ok, total = pg.insert_many(
    params={"table": "locations"},
    records=[
        {"name": "Point A", "geom": PostGISValue("ST_MakePoint", [-5.9, 37.4])},
        {"name": "Point B", "geom": PostGISValue("ST_MakePoint", [-5.8, 37.5])},
    ],
)
```

### UPDATE

```python
ok, rows = pg.update({
    "table": "locations",
    "values": {
        "geom": PostGISValue("ST_GeomFromText", ["POINT(-5.8 37.5)", 4326]),
    },
    "filters": {
        "where": [{"field": "id", "operator": "=", "value": 42}],
    },
})
```

### Bulk UPDATE

```python
ok, total = pg.update_many(
    params={
        "table": "locations",
        "value_keys": ["geom"],
        "filter_keys": ["id"],
    },
    records=[
        {"id": 1, "geom": PostGISValue("ST_GeomFromText", ["POINT(-5.9 37.4)", 4326])},
        {"id": 2, "geom": PostGISValue("ST_GeomFromText", ["POINT(-5.8 37.5)", 4326])},
    ],
)
```

### DELETE

```python
from postgresql_interactor import PostGISCondition

ok, rows = pg.delete({
    "table": "locations",
    "filters": {
        "where": [
            PostGISCondition(
                "ST_Within",
                ["geom", "ST_MakeEnvelope(%s,%s,%s,%s,4326)"],
                values=[-6.0, 37.3, -5.8, 37.5],
            ),
        ],
    },
})
```

## KNN ordering (geometric distance)

```python
from postgresql_interactor import PostGISKnnOrder

results = pg.select({
    "table": "points AS p",
    "fields": ["p.id", "p.name"],
    "filters": {
        "order_by": [
            {
                "knn": PostGISKnnOrder(
                    "p.geom",
                    "ST_Transform(ST_SetSRID(ST_MakePoint(%s,%s), %s), %s)",
                    values=[-5.9, 37.4, 4326, 25830],
                ),
                "direction": "ASC",
            },
        ],
        "limit": 10,
    },
})
```

## Exceptions

The package defines specific exceptions:

| Exception                    | Description                                 |
|------------------------------|---------------------------------------------|
| `PostgreSQLInteractorError`  | Base class for all exceptions               |
| `ValidationError`            | Input validation error                      |
| `ConfigurationError`         | Missing or invalid database configuration   |
| `QueryExecutionError`        | Query execution error                       |
| `PostGISError`               | PostGIS function or argument not allowed    |

## Security

The interactor enforces strict whitelists:

- Only **tables that exist** in the database can be used (`information_schema.tables`)
- Only **columns that exist** in those tables can be used (`information_schema.columns`)
- Only **safe SQL operators** are allowed (`=`, `<`, `IN`, `BETWEEN`, `LIKE`, etc.)
- Only **registered PostGIS functions** from the whitelists are allowed
- All **values are parameterized** with `%s` (never interpolated directly)

DELETE without WHERE is not allowed to prevent accidental deletes.

## Project structure

```
PostgreSQL-Interactor/
├── src/
│   └── postgresql_interactor/
│       ├── __init__.py           # Public API
│       ├── interactor.py         # Main PostgreSQLInteractor class
│       ├── postgis_types.py      # PostGIS types (Field, Condition, Value, KnnOrder)
│       ├── postgis_registry.py   # PostGIS function whitelists
│       ├── exceptions.py         # Custom exceptions
│       └── config.py             # .env configuration (optional, requires pydantic)
├── pyproject.toml
├── LICENSE
└── README.md
```

## License

MIT
