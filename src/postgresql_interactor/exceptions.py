from typing import Optional


class PostgreSQLInteractorError(Exception):
    """Base exception for the package."""
    pass


class ValidationError(PostgreSQLInteractorError):
    """Raised when input validation fails (table, field, operator, alias, etc.)."""
    pass


class ConfigurationError(PostgreSQLInteractorError):
    """Raised when database configuration is missing or invalid."""
    pass


class QueryExecutionError(PostgreSQLInteractorError):
    """Raised when a database query fails."""
    pass


class PostGISError(PostgreSQLInteractorError):
    """Raised when a PostGIS function or argument is not allowed."""

    def __init__(self, message: str, function: Optional[str] = None) -> None:
        super().__init__(message)
        self.function = function
