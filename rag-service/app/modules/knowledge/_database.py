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


BASELINE_REVISION = '0001_legacy_v2'
REVISION = '0002_question_history'
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


def _url(settings: DatabaseSettings, database: str | None) -> URL:
    return URL.create('mysql+pymysql', username=settings.mysql_user,
                      password=settings.mysql_password.get_secret_value(), host=settings.mysql_host,
                      port=settings.mysql_port, database=database, query={'charset': 'utf8mb4'})


class Database:
    def __init__(self, settings: DatabaseSettings):
        self._settings = settings
        self._schema_verified = False
        self._engine = create_engine(
            _url(settings, settings.mysql_database),
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
                raise SchemaMismatch('database requires initialization, adoption, or an explicit upgrade')
            verify_schema(connection)
            self._schema_verified = True

    @staticmethod
    def _revision(connection):
        if 'alembic_version' not in inspect(connection).get_table_names():
            return None
        rows = tuple(connection.execute(text('SELECT version_num FROM alembic_version')).scalars())
        if len(rows) != 1 or rows[0] not in (BASELINE_REVISION, REVISION):
            raise SchemaMismatch('unknown or incomplete Alembic revision')
        return rows[0]

    def initialize(self):
        with self.transaction() as connection:
            revision = self._revision(connection)
            if revision == REVISION:
                verify_schema(connection)
                return
            if revision == BASELINE_REVISION:
                raise SchemaMismatch('database requires an explicit upgrade-db')
            if inspect(connection).get_table_names():
                raise SchemaMismatch('database is not empty; use adopt-legacy-db after verification')
            command.upgrade(self._alembic(connection), REVISION)
            verify_schema(connection)

    def _create_missing_database(self):
        name = self._settings.mysql_database
        server = create_engine(_url(self._settings, None), connect_args={'connect_timeout': 3}, hide_parameters=True)
        try:
            with server.begin() as connection:
                exists = connection.execute(text(
                    'SELECT 1 FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = :name'), {'name': name}).first()
                if exists is None:
                    quoted = name.replace('`', '``')
                    connection.execute(text(
                        f'CREATE DATABASE `{quoted}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci'))
        except SQLAlchemyError as failure:
            raise DatabaseUnavailable('数据库未能创建') from failure
        finally:
            server.dispose()

    def prepare(self):
        """Create a missing database, then bring an empty or Alembic-managed one to the current revision.

        Unmanaged non-empty databases (such as a legacy Flyway V2 schema) are left untouched for
        an explicit, verified adopt-legacy-db.
        """
        self._create_missing_database()
        with self.transaction() as connection:
            managed = self._revision(connection) is not None
            empty = not inspect(connection).get_table_names()
        if empty:
            self.initialize()
        elif managed:
            self.upgrade()
        else:
            raise SchemaMismatch('database is not managed by this service; verify it and run adopt-legacy-db')

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
            verify_schema(connection, include_history=False)
            if self._revision(connection) is None:
                command.stamp(self._alembic(connection), BASELINE_REVISION)

    def upgrade(self):
        with self.transaction() as connection:
            revision = self._revision(connection)
            if revision is None:
                raise SchemaMismatch('database must be initialized or adopted before upgrade-db')
            verify_schema(connection, include_history=revision == REVISION)
            if revision != REVISION:
                command.upgrade(self._alembic(connection), REVISION)
                verify_schema(connection)

    def health(self):
        try:
            with self.transaction() as connection:
                if self._revision(connection) != REVISION:
                    raise SchemaMismatch('database requires initialization, adoption, or an explicit upgrade')
                verify_schema(connection)
        except DatabaseUnavailable as failure:
            self._schema_verified = False
            return {'status': 'DOWN', 'error': type(failure).__name__}
        return {'status': 'UP'}

    def close(self):
        self._engine.dispose()
