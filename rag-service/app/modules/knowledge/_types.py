"""Knowledge-owned immutable records and boundary errors."""

from dataclasses import dataclass
from datetime import datetime


class InputRejected(ValueError):
    pass


class DocumentNotFound(LookupError):
    def __init__(self):
        super().__init__("资料不存在或已删除")


class IndexStateConflict(RuntimeError):
    pass


class DatabaseUnavailable(RuntimeError):
    pass


class SchemaMismatch(DatabaseUnavailable):
    pass


@dataclass(frozen=True)
class NewDocument:
    source_uri: str
    title: str
    content: str
    content_hash: str
    tags: tuple[str, ...]
    source_type: str = "UPLOAD"


@dataclass(frozen=True)
class Document:
    id: int
    source_type: str
    source_uri: str
    title: str
    content: str
    content_hash: str
    tags: tuple[str, ...]
    index_status: str
    index_error: str | None
    chunk_count: int
    created_at: datetime
    updated_at: datetime
    indexed_at: datetime | None = None
    deleted_at: datetime | None = None


@dataclass(frozen=True)
class DocumentSummary:
    id: int
    title: str
    source_type: str
    tags: tuple[str, ...]
    index_status: str
    chunk_count: int
    updated_at: datetime


@dataclass(frozen=True)
class DocumentPage:
    total: int
    items: tuple[DocumentSummary, ...]


@dataclass(frozen=True)
class ChunkWrite:
    seq: int
    text: str
    byte_start: int
    byte_end: int
    heading_path: str
    token_count: int


@dataclass(frozen=True)
class StoredChunk:
    id: int
    document_id: int
    seq: int
    text: str
    byte_start: int
    byte_end: int
    heading_path: str | None
    token_count: int


@dataclass(frozen=True)
class Source:
    chunk_id: int
    document_id: int
    title: str
    text: str
    byte_start: int
    byte_end: int
    heading_path: str | None


@dataclass(frozen=True)
class UpdateResult:
    document: Document
    changed: bool


@dataclass(frozen=True)
class DocumentSnapshot:
    document: Document
    chunks: tuple[StoredChunk, ...]
    chunks_valid: bool
