"""Public boundary for document truth, database ownership, and index lifecycle state."""

from ._intake import content_hash, prepare_upload, validate_content
from ._database import DatabaseSettings
from ._repository import Knowledge
from ._types import (
    ChunkWrite, DatabaseUnavailable, Document, DocumentNotFound, DocumentPage,
    DocumentSnapshot, DocumentSummary, IndexStateConflict, InputRejected, NewDocument, SchemaMismatch,
    Source, StoredChunk, UpdateResult,
)
