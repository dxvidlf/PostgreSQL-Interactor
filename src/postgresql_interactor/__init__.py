"""
PostgreSQL-Interactor

A secure PostgreSQL wrapper with full PostGIS support.
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

__all__ = [
    "PostgreSQLInteractor",
    "PostGISField",
    "PostGISCondition",
    "PostGISValue",
    "PostGISKnnOrder",
    "PostgreSQLInteractorError",
    "ValidationError",
    "ConfigurationError",
    "QueryExecutionError",
    "PostGISError",
]
