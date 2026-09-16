"""SQL schema and legacy verification, private to the knowledge module."""

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, Integer, MetaData, Table, inspect, text
from sqlalchemy.dialects.mysql import CHAR, ENUM, JSON, LONGTEXT, TEXT, VARCHAR

from ._types import SchemaMismatch


metadata = MetaData()
document = Table(
    'document', metadata,
    Column('id', BigInteger, primary_key=True, autoincrement=True),
    Column('source_type', ENUM('UPLOAD', 'URL'), nullable=False),
    Column('source_uri', VARCHAR(1024), nullable=False),
    Column('title', VARCHAR(512), nullable=False),
    Column('content', LONGTEXT, nullable=False),
    Column('content_hash', CHAR(64), nullable=False),
    Column('tags', JSON, server_default=text('(JSON_ARRAY())')),
    Column('index_status', ENUM('PENDING', 'INDEXING', 'INDEXED', 'FAILED'), nullable=False, server_default='PENDING'),
    Column('index_error', VARCHAR(1024)),
    Column('chunk_count', Integer, nullable=False, server_default='0'),
    Column('created_at', DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP')),
    Column('updated_at', DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP')),
    Column('indexed_at', DateTime),
    Column('deleted_at', DateTime),
    Index('idx_index_status', 'index_status'),
    Index('idx_deleted_at', 'deleted_at'),
)
chunk = Table(
    'chunk', metadata,
    Column('id', BigInteger, primary_key=True, autoincrement=True),
    Column('document_id', BigInteger, ForeignKey('document.id', name='fk_chunk_document', ondelete='CASCADE'), nullable=False),
    Column('seq', Integer, nullable=False),
    Column('text', TEXT, nullable=False),
    Column('char_start', Integer, nullable=False),
    Column('char_end', Integer, nullable=False),
    Column('heading_path', VARCHAR(512)),
    Column('token_count', Integer, nullable=False, server_default='0'),
    Column('created_at', DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP')),
    Index('uk_document_seq', 'document_id', 'seq', unique=True),
)
question_history = Table(
    'question_history', metadata,
    Column('id', BigInteger, primary_key=True, autoincrement=True),
    Column('question', TEXT, nullable=False),
    Column('answer', LONGTEXT, nullable=False),
    Column('status', ENUM('ANSWERED', 'PARTIAL', 'REFUSED'), nullable=False),
    Column('created_at', DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP')),
    Column('elapsed_ms', Integer, nullable=False),
    Column('model', JSON, nullable=False),
    Column('sources', JSON, nullable=False),
    Column('trace', JSON, nullable=False),
    Index('idx_question_history_created', 'created_at', 'id'),
)



def _default(value):
    return None if value is None else str(value).lower().replace('(', '').replace(')', '').strip("'")


def verify_schema(connection, *, include_history=True):
    inspector = inspect(connection)
    tables = (document, chunk, question_history) if include_history else (document, chunk)
    for table in tables:
        options = connection.execute(text('''SELECT ENGINE, TABLE_COLLATION
            FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:name'''),
            {'name': table.name}).first()
        if options != ('InnoDB', 'utf8mb4_unicode_ci'):
            raise SchemaMismatch(f'{table.name}: expected InnoDB / utf8mb4_unicode_ci')
        rows = connection.execute(text('''SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE,
                COLUMN_DEFAULT, EXTRA, COLLATION_NAME FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:name'''), {'name': table.name}).mappings()
        columns = {row['COLUMN_NAME']: row for row in rows}
        if set(columns) != set(table.c.keys()):
            raise SchemaMismatch(f'{table.name}: unexpected columns')
        for expected in table.c:
            actual = columns[expected.name]
            expected_type = expected.type.compile(dialect=connection.dialect)
            if isinstance(expected.type, ENUM):
                expected_type = expected_type.replace('ENUM', 'enum', 1)
            else:
                expected_type = expected_type.lower().replace('integer', 'int')
            default = None if expected.server_default is None else expected.server_default.arg
            extra = actual['EXTRA'].lower().replace('default_generated', '').strip()
            if (actual['COLUMN_TYPE'] != expected_type
                    or (actual['IS_NULLABLE'] == 'YES') != expected.nullable
                    or _default(actual['COLUMN_DEFAULT']) != _default(default)
                    or extra != ('auto_increment' if expected.primary_key else '')):
                raise SchemaMismatch(f'{table.name}.{expected.name}: incompatible definition')
            if isinstance(expected.type, (CHAR, VARCHAR, TEXT, LONGTEXT, ENUM)):
                if actual['COLLATION_NAME'] != 'utf8mb4_unicode_ci':
                    raise SchemaMismatch(f'{table.name}.{expected.name}: incompatible collation')
        if inspector.get_pk_constraint(table.name)['constrained_columns'] != ['id']:
            raise SchemaMismatch(f'{table.name}: incompatible primary key')
        actual_indexes = {(i['name'], tuple(i['column_names']), bool(i['unique']))
                          for i in inspector.get_indexes(table.name)}
        expected_indexes = {(i.name, tuple(c.name for c in i.columns), bool(i.unique)) for i in table.indexes}
        if actual_indexes != expected_indexes:
            raise SchemaMismatch(f'{table.name}: incompatible indexes')
        foreign_keys = inspector.get_foreign_keys(table.name)
        if table is not chunk and foreign_keys:
            raise SchemaMismatch(f'{table.name}: unexpected foreign keys')
        if table is chunk:
            if len(foreign_keys) != 1:
                raise SchemaMismatch('chunk: missing document ownership constraint')
            key = foreign_keys[0]
            if (key['constrained_columns'] != ['document_id'] or key['referred_table'] != 'document'
                    or key['referred_columns'] != ['id'] or key.get('referred_schema') is not None
                    or key.get('options', {}).get('onupdate') not in (None, 'RESTRICT', 'NO ACTION')
                    or key.get('options', {}).get('ondelete') != 'CASCADE'):
                raise SchemaMismatch('chunk: incompatible document ownership constraint')
