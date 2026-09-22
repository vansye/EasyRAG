"""Private owner of the persistent vector index and its write serialization."""

from __future__ import annotations

import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings
from chromadb.errors import NotFoundError

from ._chunking import _embedding_text
from ._embedding import IndexChunk, _positive_id
from ._lexical import LexicalIndex, fuse_rankings, tokenize
from ._settings import RetrievalSettings


_PLATFORM = sys.platform


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Same quantity Chroma reports as 1 − cosine distance, for candidates the vector query did not return."""
    dot = left_norm = right_norm = 0.0
    for a, b in zip(left, right, strict=True):
        a, b = float(a), float(b)
        dot += a * b
        left_norm += a * a
        right_norm += b * b
    if not left_norm or not right_norm:
        return 0.0
    return dot / math.sqrt(left_norm * right_norm)


def persistence_path_is_safe(path: Path, platform: str | None = None) -> bool:
    """Observed with chromadb 1.5.9 on Windows (#74): under a directory containing non-ASCII
    characters the HNSW .bin files are never written, while the metadata pickle, the segment
    sequence number and the log purge all proceed, so the vectors vanish on the next restart.
    The sqlite and pickle writers cope with the path; the bundled C++ hnswlib behind the Rust
    bindings does not, presumably through a narrow-character file API. ASCII-only is the
    conservative rule until upstream persists correctly (see the canary in test_retrieval)."""
    return (platform or _PLATFORM) != "win32" or str(path).isascii()


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


class UnsafePersistencePath(RuntimeError):
    """The Chroma directory would lose HNSW files silently on this platform."""


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
        self._lexical: LexicalIndex | None = None
        self._lexical_tokens: dict[str, tuple[str, list[str]]] = {}
        try:
            self._client = self._open_client()
            self._collection = self._client.get_or_create_collection(
                name=settings.collection_name,
                metadata={"hnsw:space": "cosine", "embedding_model": settings.embedding_model,
                          "embedding_dim": settings.embedding_dim},
            )
            self._assert_metadata_matches()
            self._rebuild_lexical()
        except Exception as exc:
            self.chroma_error = type(exc).__name__
            self._collection = None

    def _rebuild_lexical(self) -> None:
        """Derive the BM25 index from the collection payload after every write, under the writer lock.

        Consistency comes from construction rather than bookkeeping: whatever Chroma holds is the
        lexical corpus. Only tokenization is cached, keyed by identifier and the text it was cut from.
        """
        result = self._collection.get(include=["documents", "metadatas"])
        documents: dict[str, list[str]] = {}
        cache: dict[str, tuple[str, list[str]]] = {}
        for identifier, text, metadata in zip(result["ids"], result["documents"], result["metadatas"]):
            lexical_text = _embedding_text(text, (metadata or {}).get("heading_path") or "")
            cached = self._lexical_tokens.get(identifier)
            tokens = cached[1] if cached is not None and cached[0] == lexical_text else tokenize(lexical_text)
            cache[identifier] = (lexical_text, tokens)
            documents[identifier] = tokens
        self._lexical_tokens = cache
        self._lexical = LexicalIndex(documents)

    def _open_client(self):
        if not persistence_path_is_safe(self._settings.chroma_dir):
            raise UnsafePersistencePath("CHROMA_DIR must be an ASCII-only path on Windows")
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
            self._rebuild_lexical()
            return result["deleted"]

    def reset(self) -> None:
        """Explicitly recreate this collection, including its physical vector dimension."""
        with self._write_lock:
            self._collection = None
            self._lexical = None
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
                self._rebuild_lexical()
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
            # Chroma 1.5.9 can retain arrays after reset; None explicitly clears old tags.
            metadatas = [
                {"document_id": document_id, "seq": sequence, "heading_path": chunk.heading_path,
                 "tags": list(chunk.tags) if chunk.tags else None}
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
                self._rebuild_lexical()
            except Exception as exc:
                cleanup_error = None
                try:
                    collection.delete(where={"document_id": document_id})
                    self._rebuild_lexical()
                except Exception as cleanup_exc:
                    cleanup_error = type(cleanup_exc).__name__
                raise IndexWriteError(type(exc).__name__, cleanup_error) from None
            return len(chunks)

    def query(
        self, question_vector: list[float], top_k: int = 5, *, lexical_query: str | None = None,
    ) -> tuple[SearchHit, ...]:
        """Dense top_k, or with lexical_query the reciprocal-rank fusion of dense and BM25 candidates.

        Every hit keeps its cosine similarity to the question; fusion only decides the order.
        """
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        with self._write_lock:
            collection = self._require_collection()
            available = collection.count()
            if not available:
                return ()
            fused = lexical_query is not None and self._lexical is not None and len(self._lexical) > 0
            depth = min(max(top_k, self._settings.retrieval_candidates) if fused else top_k, available)
            result = collection.query(
                query_embeddings=[question_vector], n_results=depth,
                include=["documents", "metadatas", "distances"],
            )
            if not result["ids"] or not result["ids"][0]:
                return ()
            rows = {
                identifier: (text, metadata, 1.0 - distance) for identifier, text, metadata, distance in zip(
                    result["ids"][0], result["documents"][0], result["metadatas"][0], result["distances"][0],
                )
            }
            order = list(rows)
            if fused:
                order = fuse_rankings([list(rows), self._lexical.rank(lexical_query, depth)])[:top_k]
                missing = [identifier for identifier in order if identifier not in rows]
                if missing:
                    extra = collection.get(ids=missing, include=["documents", "metadatas", "embeddings"])
                    for identifier, text, metadata, embedding in zip(
                        extra["ids"], extra["documents"], extra["metadatas"], extra["embeddings"],
                    ):
                        rows[identifier] = (text, metadata, _cosine_similarity(question_vector, embedding))
        hits = []
        for identifier in order:
            text, metadata, score = rows[identifier]
            chunk_id = int(identifier)
            document_id = metadata["document_id"]
            _positive_id(chunk_id, "chunk_id")
            _positive_id(document_id, "document_id")
            hits.append(SearchHit(chunk_id, document_id, text, metadata.get("heading_path", ""), score))
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
