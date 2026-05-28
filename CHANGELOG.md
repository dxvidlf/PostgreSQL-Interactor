# Changelog

All notable changes to this project are documented here.  
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [2.0.1] — 2026-05-28

### Fixed

- `dict` column values (e.g. JSONB fields) were not serialised to JSON before
  being bound as `%s` parameters in `update()`, `update_many()`, and
  `__build_where_clause()` (scalar operators, `IN`/`NOT IN`, `BETWEEN`),
  causing psycopg to raise `cannot adapt type 'dict'`.  All four code paths
  now apply `json.dumps` consistently, matching the existing behaviour in
  `insert()` and `insert_many()`.

---

## [2.0.0] — 2026-05-28

### Added

- **Pydantic v2 parameter schemas** (`schemas.py`) for every public method:
  `SelectParams`, `InsertParams`, `UpdateParams`, `InsertManyParams`,
  `UpdateManyParams`, `DeleteParams`, `Filters`, `WhereCondition`,
  `JoinClause`, `OrderByClause`.
- **Flexible input coercions** — all list fields accept a single item as
  shorthand (e.g. `fields="name"` instead of `fields=["name"]`; same for
  `joins`, `where`, `order_by`, `group_by`, `on_conflict`, `value_keys`,
  `filter_keys`). Single objects and dicts are promoted to one-element lists
  automatically.
- **Nested / subquery support** via three new schema types:
  - `Subquery` — derived-table source in FROM (`SELECT … FROM (SELECT …) AS alias`).
  - `SubqueryCondition` — `WHERE field IN (SELECT …)` and similar.
  - `ExistsCondition` — `WHERE [NOT] EXISTS (SELECT …)`.
  Subqueries are built recursively, so nesting is unlimited.
- **OFFSET support** added to `Filters` (`offset: int ≥ 0`).
- **`reload_schema()`** public method to refresh the table/column allow-lists
  after running migrations, without re-instantiating the class.
- **`py.typed` marker** (PEP 561) so mypy and pyright recognise the package
  as fully typed.
- **Unit test suite** (`tests/unit/test_schemas.py`) — 52 tests covering
  schema coercions and validation rules; no database connection required.
- **`CHANGELOG.md`** — this file.

### Changed

- All public methods now accept either a typed schema object **or** a plain
  `dict` (backward-compatible). Dicts are coerced via `model_validate`.
- `QueryExecutionError` is now **raised** on database-level failures instead
  of silently returning `(False, 0)`.  Callers should wrap write operations in
  a `try/except QueryExecutionError` block.
- `postgis_types.py` — `PostGISField`, `PostGISCondition`, `PostGISValue`,
  and `PostGISKnnOrder` converted from plain classes to `@dataclass`.
  Positional-argument interface is unchanged; `__repr__` and `__eq__` are now
  available for free.
- Module-level `__ALLOWED_*` constants renamed to `_ALLOWED_*` to avoid
  Python's double-underscore name-mangling inside class bodies.
- `config.py` — added `extra="ignore"` to `SettingsConfigDict` so extra
  variables in `.env` files (e.g. `DEBUG`, `API_KEY`) no longer cause a
  validation error.
- `pyproject.toml` — bumped to `2.0.0`; added `[dev]` optional dependency
  group and `[tool.pytest.ini_options]`.

### Migration guide (1.x → 2.x)

The only breaking change is error handling on write operations:

```python
# Before (1.x) — errors returned as (False, 0)
ok, n = db.insert(params)
if not ok:
    print("Something went wrong")

# After (2.x) — errors raised as QueryExecutionError
from postgresql_interactor import QueryExecutionError

try:
    ok, n = db.insert(params)
except QueryExecutionError as e:
    print(f"Database error: {e}")
```

Everything else is backward-compatible: existing `dict`-based call sites
continue to work without modification.

---

## [1.0.0] — 2026-05-27

Initial release.

### Added

- `PostgreSQLInteractor` class with full CRUD: `select`, `insert`,
  `insert_many`, `update`, `update_many`, `delete`.
- Schema-aware validation against `information_schema` (tables and columns).
- PostGIS function whitelists for SELECT, WHERE, and INSERT/UPDATE contexts.
- JOIN support (INNER, LEFT, RIGHT, FULL, CROSS) with ON clause validation.
- GROUP BY, ORDER BY (plain, PostGIS expression, KNN `<->`), LIMIT.
- `PostGISField`, `PostGISCondition`, `PostGISValue`, `PostGISKnnOrder` types.
- Optional `.env` configuration via `pydantic-settings`.
- Custom exception hierarchy: `ValidationError`, `ConfigurationError`,
  `QueryExecutionError`, `PostGISError`.
