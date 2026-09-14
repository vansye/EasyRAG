"""Document and chunk use cases; all SQL and transactions stay within this module."""

from ._database import Database, DatabaseSettings
from ._types import DatabaseUnavailable


class Knowledge:
    def __init__(self, settings: DatabaseSettings | None = None):
        self._database: Database | None = None
        try:
            self._database = Database(settings if settings is not None else DatabaseSettings())
        except ValueError:
            pass  # Invalid local settings are reported by health without stopping other modules.

    def _require_database(self) -> Database:
        if self._database is None:
            raise DatabaseUnavailable('数据库配置无效')
        return self._database

    def initialize_database(self):
        self._require_database().initialize()

    def adopt_legacy_database(self):
        self._require_database().adopt()

    def health(self):
        if self._database is None:
            return {'status': 'DOWN', 'error': 'INVALID_CONFIGURATION'}
        return self._database.health()

    def close(self):
        if self._database is not None:
            self._database.close()
