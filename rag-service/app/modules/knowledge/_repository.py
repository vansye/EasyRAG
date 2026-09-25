"""Document and chunk use cases; all SQL and transactions stay within this module."""

from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import asdict

from sqlalchemy import delete, func, insert, select, update

from . import _history
from ._database import Database, DatabaseSettings
from ._history import HistoryPage, HistoryRecord, HistoryWrite
from ._intake import content_hash, java_strip, metadata, prepare_upload, validate_content
from ._schema import chunk, document
from ._types import (
    ChunkWrite, DatabaseUnavailable, Document, DocumentNotFound, DocumentPage, DocumentSnapshot, DocumentSummary,
    IndexStateConflict, InputRejected, Source, StoredChunk, UpdateResult,
)
from ._validation import validate_chunks, validate_index_error


def _tags(value):
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(tag, str) for tag in value):
        raise DatabaseUnavailable('document tags must be an array of strings')
    return tuple(value)


def _document_query(*columns):
    count = select(func.count()).where(chunk.c.document_id == document.c.id).correlate(document).scalar_subquery()
    return select(*(columns or (document,)), count.label('actual_chunk_count')).where(document.c.deleted_at.is_(None))


def _document_record(row):
    values = {column.name: row[column.name] for column in document.c}
    values['tags'] = _tags(row['tags'])
    values['chunk_count'] = row['actual_chunk_count']
    return Document(**values)


def _get(connection, document_id, *, lock=False):
    query = _document_query().where(document.c.id == document_id)
    row = connection.execute(query.with_for_update() if lock else query).mappings().first()
    if row is None:
        raise DocumentNotFound()
    return _document_record(row)


def _chunks(connection, document_id):
    rows = connection.execute(select(chunk).where(chunk.c.document_id == document_id).order_by(chunk.c.seq)).mappings()
    return tuple(_chunk_record(row) for row in rows)


def _chunk_record(row):
    return StoredChunk(row['id'], row['document_id'], row['seq'], row['text'], row['char_start'],
                       row['char_end'], row['heading_path'], row['token_count'])


def _chunk_writes(chunks):
    return tuple(ChunkWrite(c.seq,c.text,c.byte_start,c.byte_end,c.heading_path,c.token_count) for c in chunks)


def _mutable(record):
    if record.index_status == 'INDEXING':
        raise IndexStateConflict('document is still indexing')


def _change(connection, document_id, **values):
    result = connection.execute(update(document).where(document.c.id == document_id,
                                                       document.c.deleted_at.is_(None)).values(**values))
    if result.rowcount != 1:
        raise IndexStateConflict('document mutation was not confirmed')


def _replace_chunks(connection, document_id, drafts):
    connection.execute(delete(chunk).where(chunk.c.document_id == document_id))
    saved = []
    for draft in drafts:
        result = connection.execute(insert(chunk).values(
            document_id=document_id, seq=draft.seq, text=draft.text, char_start=draft.byte_start,
            char_end=draft.byte_end, heading_path=draft.heading_path, token_count=draft.token_count,
        ))
        saved.append(StoredChunk(result.inserted_primary_key[0], document_id, draft.seq, draft.text,
                                 draft.byte_start, draft.byte_end, draft.heading_path, draft.token_count))
    _change(connection, document_id, chunk_count=len(saved))
    return tuple(saved)


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

    @contextmanager
    def _transaction(self):
        database = self._require_database()
        with database.transaction() as connection:
            database.require_schema(connection)
            yield connection

    def create(self, filename: str, content_bytes: bytes) -> Document:
        prepared = prepare_upload(filename, content_bytes)
        with self._transaction() as connection:
            result = connection.execute(insert(document).values(**asdict(prepared)))
            return _get(connection, result.inserted_primary_key[0])

    def list(self, page=0, size=20, status=None, q=None) -> DocumentPage:
        if type(page) is not int or page < 0:
            raise InputRejected('page 不能为负数')
        if type(size) is not int or not 1 <= size <= 100:
            raise InputRejected('size 须在 1 到 100 之间')
        normalized = status.upper() if status and java_strip(status) else None
        if normalized is not None and normalized not in {'PENDING', 'INDEXING', 'INDEXED', 'FAILED'}:
            raise InputRejected('status 只能是 PENDING / INDEXING / INDEXED / FAILED')
        conditions = [document.c.deleted_at.is_(None)]
        if normalized:
            conditions.append(document.c.index_status == normalized)
        if q and java_strip(q):
            conditions.append(func.locate(java_strip(q), document.c.title) > 0)
        with self._transaction() as connection:
            total = connection.execute(select(func.count()).select_from(document).where(*conditions)).scalar_one()
            query = (_document_query(document.c.id, document.c.title, document.c.source_type, document.c.tags,
                                     document.c.index_status, document.c.updated_at).where(*conditions)
                     .order_by(document.c.updated_at.desc(), document.c.id.desc()).limit(size).offset(page * size))
            items = tuple(DocumentSummary(row['id'], row['title'], row['source_type'], _tags(row['tags']),
                                          row['index_status'], row['actual_chunk_count'], row['updated_at'])
                          for row in connection.execute(query).mappings())
        return DocumentPage(total, items)

    def get(self, document_id: int) -> Document:
        with self._transaction() as connection:
            return _get(connection, document_id)

    def chunks(self, document_id: int) -> tuple[StoredChunk, ...]:
        with self._transaction() as connection:
            _get(connection, document_id)
            return _chunks(connection, document_id)

    def update(self, document_id: int, content: str) -> UpdateResult:
        validate_content(content)
        digest = content_hash(content)
        with self._transaction() as connection:
            current = _get(connection, document_id, lock=True)
            _mutable(current)
            changed = digest != current.content_hash
            if changed:
                fallback = (current.source_uri.rsplit('.', 1)[0]
                            if current.source_type == 'UPLOAD' else current.title)
                title, tags = metadata(content, fallback)
                _change(connection, document_id, content=content, content_hash=digest, title=title,
                        tags=list(tags), index_status='PENDING', index_error=None, chunk_count=0,
                        updated_at=func.current_timestamp())
                connection.execute(delete(chunk).where(chunk.c.document_id == document_id))
            else:
                _change(connection, document_id, updated_at=func.current_timestamp())
            return UpdateResult(_get(connection, document_id), changed)

    def prepare_reindex(self, document_id: int) -> Document:
        with self._transaction() as connection:
            _mutable(_get(connection, document_id, lock=True))
            _change(connection, document_id, index_status='PENDING', index_error=None, chunk_count=0)
            connection.execute(delete(chunk).where(chunk.c.document_id == document_id))
            return _get(connection, document_id)

    def delete(self, document_id: int):
        with self._transaction() as connection:
            _mutable(_get(connection, document_id, lock=True))
            _change(connection, document_id, deleted_at=func.current_timestamp(), chunk_count=0)
            connection.execute(delete(chunk).where(chunk.c.document_id == document_id))

    def begin_indexing(self, document_id: int, chunks: Sequence[ChunkWrite], *, expected_content: str | None = None) -> tuple[StoredChunk, ...]:
        drafts = tuple(chunks)
        with self._transaction() as connection:
            current = _get(connection, document_id, lock=True)
            if current.index_status != 'PENDING':
                raise IndexStateConflict('only PENDING documents can begin indexing')
            if expected_content is not None and expected_content != current.content:
                raise IndexStateConflict('document content changed since chunking')
            validate_chunks(current.content, drafts)
            _change(connection, document_id, index_status='INDEXING', index_error=None)
            return _replace_chunks(connection, document_id, drafts)

    def mark_indexed(self, document_id: int):
        self._transition(document_id, ('INDEXING',), index_status='INDEXED', index_error=None,
                         indexed_at=func.current_timestamp())

    def mark_failed(self, document_id: int, error: str):
        validate_index_error(error)
        self._transition(document_id, ('PENDING', 'INDEXING'), index_status='FAILED', index_error=error)

    def _transition(self, document_id, eligible, **values):
        if type(document_id) is not int or document_id <= 0:
            raise InputRejected('document_id must be positive')
        with self._transaction() as connection:
            result = connection.execute(update(document).where(
                document.c.id == document_id, document.c.deleted_at.is_(None),
                document.c.index_status.in_(eligible),
            ).values(**values))
            if result.rowcount != 1:
                raise IndexStateConflict('document is missing, deleted, or not in an eligible indexing state')

    def pending_ids(self) -> tuple[int, ...]:
        with self._transaction() as connection:
            return tuple(connection.execute(select(document.c.id).where(
                document.c.deleted_at.is_(None), document.c.index_status == 'PENDING',
            ).order_by(document.c.id)).scalars())

    def sources(self, chunk_ids: Sequence[int]) -> tuple[Source, ...]:
        if not chunk_ids:
            return ()
        with self._transaction() as connection:
            rows = connection.execute(select(chunk, document.c.title).join(document).where(
                chunk.c.id.in_(tuple(chunk_ids)), document.c.deleted_at.is_(None),
            ).order_by(chunk.c.id)).mappings()
            return tuple(Source(row['id'], row['document_id'], row['title'], row['text'], row['char_start'],
                                row['char_end'], row['heading_path']) for row in rows)

    def snapshots(self) -> tuple[DocumentSnapshot, ...]:
        """Return data and A's own byte/sequence validation for consistency inspection."""
        with self._transaction() as connection:
            documents = tuple(_document_record(row) for row in connection.execute(
                _document_query().order_by(document.c.id)).mappings())
            by_document = {}
            rows = connection.execute(select(chunk).join(document).where(document.c.deleted_at.is_(None))
                                      .order_by(chunk.c.document_id, chunk.c.seq)).mappings()
            for row in rows:
                by_document.setdefault(row['document_id'], []).append(_chunk_record(row))
            snapshots = []
            for current in documents:
                chunks = tuple(by_document.get(current.id, ()))
                valid = True
                try:
                    validate_chunks(current.content, _chunk_writes(chunks))
                except InputRejected:
                    valid = False
                snapshots.append(DocumentSnapshot(current, chunks, valid))
            return tuple(snapshots)

    def begin_rebuild(self, document_id: int, chunks: Sequence[ChunkWrite], *, expected_content: str) -> tuple[StoredChunk, ...]:
        """Offline recovery entrypoint: keep IDs only when the current split matches exactly."""
        drafts = tuple(chunks)
        with self._transaction() as connection:
            current = _get(connection, document_id, lock=True)
            if current.content != expected_content:
                raise IndexStateConflict('document content changed since recovery snapshot')
            validate_chunks(current.content, drafts)
            existing = _chunks(connection, document_id)
            _change(connection, document_id, index_status='INDEXING', index_error=None)
            if _chunk_writes(existing) == drafts:
                _change(connection, document_id, chunk_count=len(existing))
                return existing
            return _replace_chunks(connection, document_id, drafts)

    def save_history(self, entry: HistoryWrite) -> HistoryRecord:
        with self._transaction() as connection:
            return _history.save(connection, entry)

    def list_history(self, page=0, size=20) -> HistoryPage:
        if type(page) is not int or page < 0:
            raise InputRejected('page 不能为负数')
        if type(size) is not int or not 1 <= size <= 100:
            raise InputRejected('size 须在 1 到 100 之间')
        with self._transaction() as connection:
            return _history.list_records(connection, page, size)

    def get_history(self, history_id: int) -> HistoryRecord:
        with self._transaction() as connection:
            return _history.get(connection, history_id)

    def delete_history(self, history_id: int) -> None:
        with self._transaction() as connection:
            _history.delete_record(connection, history_id)

    def prepare_database(self):
        self._require_database().prepare()

    def initialize_database(self):
        self._require_database().initialize()

    def adopt_legacy_database(self):
        self._require_database().adopt()

    def upgrade_database(self):
        self._require_database().upgrade()

    def health(self):
        if self._database is None:
            return {'status': 'DOWN', 'error': 'INVALID_CONFIGURATION'}
        return self._database.health()

    def close(self):
        if self._database is not None:
            self._database.close()
