"""Run explicitly with MySQL credentials; never falls back to SQLite or the business DB."""

from pathlib import Path

import pytest

from app.modules.knowledge import public as knowledge
from tests.mysql_support import mysql_sandbox


LEGACY = Path(__file__).resolve().parents[1] / 'app/modules/knowledge/migrations/legacy'


def install_legacy(sandbox):
    with sandbox.connect() as connection, connection.cursor() as cursor:
        for name in ('V1__init.sql', 'V2__fix_updated_at_and_unique_seq.sql'):
            sql = LEGACY.joinpath(name).read_text(encoding='utf-8-sig')
            for statement in sql.split(';'):
                if statement.strip():
                    cursor.execute(statement)
        cursor.execute('''CREATE TABLE flyway_schema_history (
            installed_rank INT PRIMARY KEY, version VARCHAR(50), type VARCHAR(20),
            script VARCHAR(1000), checksum INT, success BOOL NOT NULL)''')
        cursor.executemany('INSERT INTO flyway_schema_history VALUES (%s, %s, %s, %s, %s, %s)', [
            (1, '1', 'SQL', 'V1__init.sql', 143109904, True),
            (2, '2', 'SQL', 'V2__fix_updated_at_and_unique_seq.sql', -2014110456, True),
        ])


def test_empty_database_requires_explicit_baseline_and_has_v2_constraints():
    with mysql_sandbox() as sandbox:
        service = knowledge.Knowledge(sandbox.settings())
        try:
            assert service.health()['status'] == 'DOWN'
            service.initialize_database()
            assert service.health()['status'] == 'UP'
            service.initialize_database()  # idempotent only for the verified current revision
            with sandbox.connect() as connection, connection.cursor() as cursor:
                cursor.execute('SELECT version_num FROM alembic_version')
                assert cursor.fetchone() == ('0001_legacy_v2',)
                cursor.execute("SELECT EXTRA FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME='document' AND COLUMN_NAME='updated_at'", (sandbox.name,))
                assert 'on update' not in cursor.fetchone()[0].lower()
                cursor.execute("SELECT NON_UNIQUE, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=%s AND TABLE_NAME='chunk' AND INDEX_NAME='uk_document_seq' GROUP BY NON_UNIQUE", (sandbox.name,))
                assert cursor.fetchone() == (0, 'document_id,seq')
        finally:
            service.close()


def test_adoption_keeps_data_ids_and_flyway_history():
    with mysql_sandbox() as sandbox:
        install_legacy(sandbox)
        with sandbox.connect() as connection, connection.cursor() as cursor:
            cursor.execute("INSERT INTO document (id,source_type,source_uri,title,content,content_hash) VALUES (41,'UPLOAD','note.md','Note','Hello',%s)", ('a' * 64,))
            cursor.execute("INSERT INTO chunk (id,document_id,seq,text,char_start,char_end,heading_path) VALUES (73,41,0,'Hello',0,5,'Note')")
        service = knowledge.Knowledge(sandbox.settings())
        try:
            with pytest.raises(knowledge.SchemaMismatch):
                service.initialize_database()
            service.adopt_legacy_database()
            service.adopt_legacy_database()
            with sandbox.connect() as connection, connection.cursor() as cursor:
                cursor.execute('SELECT id,content FROM document')
                assert cursor.fetchall() == ((41, 'Hello'),)
                cursor.execute('SELECT id,document_id FROM chunk')
                assert cursor.fetchall() == ((73, 41),)
                cursor.execute('SELECT version,checksum,success FROM flyway_schema_history ORDER BY installed_rank')
                assert cursor.fetchall() == (('1',143109904,1), ('2',-2014110456,1))
        finally:
            service.close()


@pytest.mark.parametrize('damage', [
    "UPDATE flyway_schema_history SET checksum=0 WHERE version='2'",
    "UPDATE flyway_schema_history SET success=0 WHERE version='2'",
    "DELETE FROM flyway_schema_history WHERE version='2'",
    "ALTER TABLE chunk DROP INDEX uk_document_seq, ADD INDEX idx_document_seq (document_id,seq)",
    "ALTER TABLE document MODIFY updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
    "ALTER TABLE chunk DROP FOREIGN KEY fk_chunk_document",
    "ALTER TABLE chunk DROP FOREIGN KEY fk_chunk_document; ALTER TABLE chunk ADD CONSTRAINT fk_chunk_document FOREIGN KEY (document_id) REFERENCES document(id) ON DELETE CASCADE ON UPDATE CASCADE",
    "ALTER TABLE document MODIFY title VARCHAR(128) NOT NULL",
    "ALTER TABLE document ADD UNIQUE KEY wrong_content_dedup (content_hash)",
], ids=['checksum','failed-history','missing-v2','missing-unique-seq','on-update','missing-fk','cascading-id','wrong-width','unexpected-unique'])
def test_adoption_refuses_unverified_legacy_schema_without_stamp(damage):
    with mysql_sandbox() as sandbox:
        install_legacy(sandbox)
        with sandbox.connect() as connection, connection.cursor() as cursor:
            for statement in damage.split(';'):
                cursor.execute(statement)
        service = knowledge.Knowledge(sandbox.settings())
        try:
            with pytest.raises(knowledge.SchemaMismatch):
                service.adopt_legacy_database()
            with sandbox.connect() as connection, connection.cursor() as cursor:
                cursor.execute("SHOW TABLES LIKE 'alembic_version'")
                assert not cursor.fetchall()
        finally:
            service.close()


def test_connection_is_lazy_and_health_does_not_expose_credentials():
    settings = knowledge.DatabaseSettings(_env_file=None, mysql_host='127.0.0.1', mysql_port=1,
                                          mysql_password='private-test-password')
    service = knowledge.Knowledge(settings)
    try:
        result = service.health()
        assert result['status'] == 'DOWN'
        assert 'private-test-password' not in repr(result)
        assert 'private-test-password' not in repr(settings)
    finally:
        service.close()
