"""
Optional environment-based configuration for PostgreSQLInteractor.

If pydantic-settings is not installed, connection parameters must be
passed directly to the PostgreSQLInteractor constructor.
"""

import logging
import os
from functools import lru_cache

logger = logging.getLogger(__name__)

_DEFAULT_ENV_FILE = ".env"


def _find_env_file() -> str:
    candidate = os.path.join(os.getcwd(), _DEFAULT_ENV_FILE)
    if os.path.isfile(candidate):
        return candidate

    package_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    candidate = os.path.join(package_dir, _DEFAULT_ENV_FILE)
    if os.path.isfile(candidate):
        return candidate

    return os.path.join(os.getcwd(), _DEFAULT_ENV_FILE)


def get_environment_variables():
    """
    Returns an object with DB_NAME, DB_IP, DB_PORT, DB_USERNAME,
    and DB_PASSWORD fields read from the .env file.

    Raises ImportError if pydantic-settings is not available.
    """
    try:
        from pydantic_settings import BaseSettings, SettingsConfigDict
    except ImportError:
        raise ImportError(
            "pydantic-settings is not installed. Install it with "
            "'pip install postgresql-interactor[pydantic]' or pass "
            "connection parameters directly to the constructor."
        )

    @lru_cache
    def _cached_env_file():
        return _find_env_file()

    class EnvironmentSettings(BaseSettings):
        DB_NAME: str
        DB_IP: str
        DB_PORT: int
        DB_USERNAME: str
        DB_PASSWORD: str

        model_config = SettingsConfigDict(
            env_file=_cached_env_file(),
            extra="ignore",
        )

    return EnvironmentSettings()
