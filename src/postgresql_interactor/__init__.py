"""
PostgreSQL-Interactor
=====================

A secure PostgreSQL wrapper with full PostGIS support.

Quick start::

    from postgresql_interactor import PostgreSQLInteractor

    db = PostgreSQLInteractor(
        db_name="mydb", ip="localhost", port=5432,
        username="user", password="secret",
    )

    rows = db.select({"table": "users", "filters": {"limit": 10}})

All query methods accept either a typed schema object or a plain ``dict``.
"""

from .exceptions import (
    ConfigurationError,
    PostGISError,
    PostgreSQLInteractorError,
    QueryExecutionError,
    ValidationError,
)
from .interactor import PostgreSQLInteractor
from .postgis_types import (
    PostGISCondition,
    PostGISField,
    PostGISKnnOrder,
    PostGISValue,
)
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

__all__ = [
    # Core
    "PostgreSQLInteractor",
    # Query schemas
    "SelectParams",
    "InsertParams",
    "UpdateParams",
    "InsertManyParams",
    "UpdateManyParams",
    "DeleteParams",
    "Filters",
    "WhereCondition",
    "JoinClause",
    "OrderByClause",
    "Subquery",
    "SubqueryCondition",
    "ExistsCondition",
    # PostGIS types
    "PostGISField",
    "PostGISCondition",
    "PostGISValue",
    "PostGISKnnOrder",
    # Exceptions
    "PostgreSQLInteractorError",
    "ValidationError",
    "ConfigurationError",
    "QueryExecutionError",
    "PostGISError",
]
