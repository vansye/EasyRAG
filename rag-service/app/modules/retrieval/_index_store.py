"""Private owner of the persistent vector index and its write serialization."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from threading import Lock
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings
from chromadb.errors import NotFoundError

from ._embedding import IndexChunk, _positive_id
from ._settings import RetrievalSettings


@dataclass(frozen=True)
class SearchHit:
    chunk_id: int
    document_id: int
    text: str
    heading_path: str
    score: float


@dataclass(frozen=True)
class IndexEntry:
    chunk_id: int
    document_id: int
    seq: int
    text: str
    heading_path: str
    tags: tuple[str, ...]


class IndexMetadataMismatch(RuntimeError):
    """The existing collection was built with another model or dimension."""


class ChunkIdConflict(ValueError):
    """A requested chunk ID belongs to another document."""


class IndexWriteError(RuntimeError):
    """A replace failed after mutation began; only exception types cross the boundary."""

    def __init__(self, cause: str, cleanup_error: str | None) -> None:
        super().__init__("index write failed")
        self.cause = cause
        self.cleanup_error = cleanup_error

    @property
    def cleanup_confirmed(self) -> bool:
        return self.cleanup_error is None


class IndexStore:
    def __init__(self, settings: RetrievalSettings) -> None:
        self._settings = settings
        self._write_lock = Lock()
        self.chroma_error: str | None = None
        self._collection = None
        self._client = None
        try:
            self._client = self._open_client()
            self._collection = self._client.get_or_create_collection(
                name=settings.collection_name,
                metadata={"hnsw:space": "cosine", "embedding_model": settings.embedding_model,
                          "embedding_dim": settings.embedding_dim},
            )
            self._assert_metadata_matches()
        except Exception as exc:
            self.chroma_error = type(exc).__name__
            self._collection = None

    def _open_client(self):
        self._settings.chroma_dir.mkdir(parents=True, exist_ok=True)
        return chromadb.PersistentClient(
            path=str(self._settings.chroma_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )

    def _assert_metadata_matches(self) -> None:
        metadata = (self._collection.metadata or {}) if self._collection else {}
        model = metadata.get("embedding_model")
        dimensions = metadata.get("embedding_dim")
        if model is not None and model != self._settings.embedding_model:
            raise IndexMetadataMismatch("collection embedding model does not match configuration")
        if dimensions is not None and int(dimensions) != self._settings.embedding_dim:
            raise IndexMetadataMismatch("collection embedding dimension does not match configuration")

    def _require_collection(self):
        if self._collection is None:
            raise RuntimeError(self.chroma_error or "NOT_INITIALIZED")
        return self._collection

    def vector_count(self) -> int:
        return self._require_collection().count()

    def delete_document(self, document_id: int) -> int:
        with self._write_lock:
            result = self._require_collection().delete(where={"document_id": document_id})
            return result["deleted"]

    def reset(self) -> None:
        """Explicitly recreate this collection, including its physical vector dimension."""
        with self._write_lock:
            self._collection = None
            try:
                if self._client is None:
                    self._client = self._open_client()
                try:
                    self._client.delete_collection(name=self._settings.collection_name)
                except NotFoundError:
                    # A previous explicit reset may have deleted it before creation failed.
                    pass
                self._collection = self._client.create_collection(
                    name=self._settings.collection_name,
                    metadata={"hnsw:space": "cosine", "embedding_model": self._settings.embedding_model,
                              "embedding_dim": self._settings.embedding_dim},
                )
                self.chroma_error = None
            except Exception as exc:
                self.chroma_error = type(exc).__name__
                self._collection = None
                raise

    def replace_document(
        self, document_id: int, chunks: Sequence[IndexChunk], vectors: list[list[float]],
    ) -> int:
        """Replace only after every embedding is validated; cleanup is explicit, not atomic."""
        with self._write_lock:
            collection = self._require_collection()
            identifiers = [str(chunk.chunk_id) for chunk in chunks]
            existing = collection.get(ids=identifiers, include=["metadatas"])
            if any(not metadata or metadata.get("document_id") != document_id
                   for metadata in existing["metadatas"]):
                raise ChunkIdConflict("chunk_id belongs to another document")
            batch_size = min(self._settings.embed_batch_size, self._client.get_max_batch_size())
            documents = [chunk.text for chunk in chunks]
            metadatas = [
                {"document_id": document_id, "seq": sequence, "heading_path": chunk.heading_path,
                 **({"tags": list(chunk.tags)} if chunk.tags else {})}
                for sequence, chunk in enumerate(chunks)
            ]
            try:
                collection.delete(where={"document_id": document_id})
                for offset in range(0, len(chunks), batch_size):
                    collection.upsert(
                        ids=identifiers[offset:offset + batch_size],
                        embeddings=vectors[offset:offset + batch_size],
                        documents=documents[offset:offset + batch_size],
                        metadatas=metadatas[offset:offset + batch_size],
                    )
            except Exception as exc:
                cleanup_error = None
                try:
                    collection.delete(where={"document_id": document_id})
                except Exception as cleanup_exc:
                    cleanup_error = type(cleanup_exc).__name__
                raise IndexWriteError(type(exc).__name__, cleanup_error) from None
            return len(chunks)

    def query(self, question_vector: list[float], top_k: int = 5) -> tuple[SearchHit, ...]:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        with self._write_lock:
            collection = self._require_collection()
            available = collection.count()
            if not available:
                return ()
            result = collection.query(
                query_embeddings=[question_vector], n_results=min(top_k, available),
                include=["documents", "metadatas", "distances"],
            )
        hits = []
        if not result["ids"] or not result["ids"][0]:
            return ()
        for identifier, text, metadata, distance in zip(
            result["ids"][0], result["documents"][0], result["metadatas"][0], result["distances"][0],
        ):
            chunk_id = int(identifier)
            document_id = metadata["document_id"]
            _positive_id(chunk_id, "chunk_id")
            _positive_id(document_id, "document_id")
            hits.append(SearchHit(chunk_id, document_id, text, metadata.get("heading_path", ""), 1.0 - distance))
        return tuple(hits)

    def inspect(self) -> tuple[IndexEntry, ...]:
        """Read the complete payload under the writer lock, without exposing Chroma objects."""
        with self._write_lock:
            result = self._require_collection().get(include=["documents", "metadatas"])
        entries = []
        for identifier, text, metadata in zip(result["ids"], result["documents"], result["metadatas"]):
            chunk_id = int(identifier)
            if str(chunk_id) != identifier:
                raise ValueError("index identifier is not a canonical chunk_id")
            document_id, sequence = metadata["document_id"], metadata["seq"]
            heading_path, tags = metadata.get("heading_path", ""), metadata.get("tags", [])
            _positive_id(chunk_id, "chunk_id")
            _positive_id(document_id, "document_id")
            if (type(sequence) is not int or sequence < 0 or not isinstance(text, str)
                    or not isinstance(heading_path, str) or not isinstance(tags, list)
                    or any(not isinstance(tag, str) for tag in tags)):
                raise ValueError("index payload does not match the snapshot contract")
            entries.append(IndexEntry(chunk_id, document_id, sequence, text, heading_path, tuple(tags)))
        return tuple(sorted(entries, key=lambda entry: (entry.document_id, entry.seq, entry.chunk_id)))

    def probe(self) -> dict[str, Any]:
        if self._collection is None:
            return {"status": "DOWN", "collection": self._settings.collection_name,
                    "error": self.chroma_error or "NOT_INITIALIZED"}
        try:
            return {"status": "UP", "collection": self._settings.collection_name,
                    "vectors": self.vector_count()}
        except Exception as exc:
            return {"status": "DOWN", "collection": self._settings.collection_name,
                    "error": type(exc).__name__}

    def close(self) -> None:
        with self._write_lock:
            if self._client is not None:
                self._client.close()
                self._client = None
            self._collection = None
