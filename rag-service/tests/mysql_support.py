"""Real MySQL fixtures; every test owns a randomly named, disposable database."""

import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import uuid4

import pymysql


@dataclass(frozen=True)
class MysqlSandbox:
    name: str

    def connect(self):
        return pymysql.connect(**connection_options(), database=self.name, autocommit=True)

    def settings(self):
        from app.modules.knowledge.public import DatabaseSettings
        options = connection_options()
        return DatabaseSettings(
            _env_file=None, mysql_host=options['host'], mysql_port=options['port'],
            mysql_user=options['user'], mysql_password=options['password'], mysql_database=self.name,
        )


def connection_options():
    return {
        'host': os.environ.get('MYSQL_HOST', '127.0.0.1'),
        'port': int(os.environ.get('MYSQL_PORT', '3306')),
        'user': os.environ.get('MYSQL_USER', 'root'),
        'password': os.environ.get('MYSQL_PASSWORD', ''),
        'charset': 'utf8mb4', 'connect_timeout': 3,
    }


@contextmanager
def mysql_sandbox():
    name = 'easyrag_fastapi_it_' + uuid4().hex
    assert re.fullmatch(r'easyrag_fastapi_it_[0-9a-f]{32}', name)
    admin = pymysql.connect(**connection_options(), autocommit=True)
    try:
        with admin.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
        yield MysqlSandbox(name)
    finally:
        try:
            with admin.cursor() as cursor:
                cursor.execute(f'DROP DATABASE IF EXISTS `{name}`')
                cursor.execute('SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s', (name,))
                assert cursor.fetchone() is None, 'isolated database was not removed'
        finally:
            admin.close()
