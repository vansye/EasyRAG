"""Lazy MySQL access and explicit, verified Alembic ownership transfer."""

from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL, create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError

from ._schema import verify_schema
from ._types import DatabaseUnavailable, SchemaMismatch


REVISION = '0001_legacy_v2'
LEGACY_HISTORY = (
    ('1', 'SQL', 'V1__init.sql', 143109904, 1),
    ('2', 'SQL', 'V2__fix_updated_at_and_unique_seq.sql', -2014110456, 1),
)


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore', hide_input_in_errors=True)
    mysql_host: str = 'localhost'
    mysql_port: int = Field(default=3306, gt=0, le=65535)
    mysql_database: str = 'easyrag'
    mysql_user: str = 'root'
    mysql_password: SecretStr = SecretStr('')


class Database:
    def __init__(self, settings: DatabaseSettings):
        self._schema_verified = False
        self._engine = create_engine(
            URL.create('mysql+pymysql', username=settings.mysql_user,
                       password=settings.mysql_password.get_secret_value(), host=settings.mysql_host,
                       port=settings.mysql_port, database=settings.mysql_database, query={'charset': 'utf8mb4'}),
            pool_pre_ping=True, pool_size=10, max_overflow=0, pool_timeout=3,
            isolation_level='READ COMMITTED',
            connect_args={'connect_timeout': 3, 'init_command': "SET time_zone = '+08:00'"},
            hide_parameters=True,
        )

    @contextmanager
    def transaction(self):
        try:
            with self._engine.begin() as connection:
                yield connection
        except SQLAlchemyError as failure:
            raise DatabaseUnavailable('数据库操作未能确认完成') from failure

    def _alembic(self, connection):
        config = Config()
        config.set_main_option('script_location', str(Path(__file__).parent / 'migrations'))
        config.attributes['connection'] = connection
        return config

    def require_schema(self, connection):
        if not self._schema_verified:
            if self._revision(connection) != REVISION:
                raise SchemaMismatch('database has not been initialized or adopted')
            verify_schema(connection)
            self._schema_verified = True

    @staticmethod
    def _revision(connection):
        if 'alembic_version' not in inspect(connection).get_table_names():
            return None
        rows = tuple(connection.execute(text('SELECT version_num FROM alembic_version')).scalars())
        if rows != (REVISION,):
            raise SchemaMismatch('unknown or incomplete Alembic revision')
        return REVISION

    def initialize(self):
        with self.transaction() as connection:
            if self._revision(connection) == REVISION:
                verify_schema(connection)
                return
            if inspect(connection).get_table_names():
                raise SchemaMismatch('database is not empty; use adopt-legacy-db after verification')
            command.upgrade(self._alembic(connection), REVISION)
            verify_schema(connection)

    def adopt(self):
        with self.transaction() as connection:
            names = set(inspect(connection).get_table_names())
            if names not in ({'document', 'chunk', 'flyway_schema_history'},
                             {'document', 'chunk', 'flyway_schema_history', 'alembic_version'}):
                raise SchemaMismatch('legacy table set does not match the supported V2 database')
            rows = tuple(connection.execute(text('''SELECT version, type, script, checksum, success
                FROM flyway_schema_history ORDER BY installed_rank''')))
            if rows != LEGACY_HISTORY:
                raise SchemaMismatch('Flyway V1/V2 history or checksums do not match')
            verify_schema(connection)
            if self._revision(connection) != REVISION:
                command.stamp(self._alembic(connection), REVISION)

    def health(self):
        try:
            with self.transaction() as connection:
                if self._revision(connection) != REVISION:
                    raise SchemaMismatch('database has not been initialized or adopted')
                verify_schema(connection)
        except DatabaseUnavailable as failure:
            self._schema_verified = False
            return {'status': 'DOWN', 'error': type(failure).__name__}
        return {'status': 'UP'}

    def close(self):
        self._engine.dispose()
