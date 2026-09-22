"""Public boundary for splitting, embedding, index maintenance, and retrieval."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from contextlib import contextmanager
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from time import perf_counter

import httpx as _httpx
from tokenizers import Tokenizer as _Tokenizer

from ._chunking import Chunk, split_markdown
from ._embedding import IndexChunk, _positive_id, embed_texts as _embed_texts, probe_embedding as _probe_embedding
from ._index_store import (
    ChunkIdConflict, IndexEntry, IndexMetadataMismatch, IndexStore as _IndexStore, IndexWriteError, SearchHit,
)
from ._lexical import LexicalIndex, fuse_rankings, tokenize
from ._settings import RetrievalSettings


ChunkDraft = Chunk

__all__ = [
    "Chunk", "ChunkDraft", "ChunkIdConflict", "IndexChunk", "IndexEntry", "IndexMetadataMismatch",
    "IndexWriteError", "LexicalIndex", "Retrieval", "RetrievalSettings", "RetrievalUnavailable", "SearchHit",
    "fuse_rankings", "index_representatives", "split_markdown", "splitter_fingerprint", "tokenize",
]


def splitter_fingerprint() -> str:
    """Reproducibility metadata for offline evaluation without exposing private paths."""
    return sha256(Path(__file__).with_name('_chunking.py').read_bytes()).hexdigest()


def index_representatives(chunks: Iterable[IndexChunk]) -> tuple[IndexChunk, ...]:
    """Keep the first chunk per identical embedding input within one document.

    Identical inputs produce identical vectors; hundreds of them degrade HNSW navigation
    until unrelated chunks become unreachable. Storage keeps every chunk row, only the
    index is deduplicated, and callers must apply the same rule when auditing the index.
    """
    seen: set[str] = set()
    kept = []
    for chunk in chunks:
        if not isinstance(chunk, IndexChunk):
            raise ValueError("chunks must contain IndexChunk records")
        if chunk.embedding_text not in seen:
            seen.add(chunk.embedding_text)
            kept.append(chunk)
    return tuple(kept)


class RetrievalUnavailable(RuntimeError):
    """A dependency failed; component and cause contain no upstream message or body."""

    def __init__(self, component: str, cause: str) -> None:
        super().__init__(f"{component} unavailable")
        self.component = component
        self.cause = cause


@contextmanager
def _measure(stage: str, record_timing: Callable[[str, float], None] | None):
    started = perf_counter()
    try:
        yield
    finally:
        if record_timing is not None:
            record_timing(stage, (perf_counter() - started) * 1000)


@lru_cache(maxsize=1)
def _load_chunk_tokenizer(path: str) -> _Tokenizer:
    tokenizer = _Tokenizer.from_file(path)
    tokenizer.no_truncation()
    tokenizer.no_padding()
    return tokenizer


class Retrieval:
    """Own local retrieval resources without knowing business data or request orchestration."""

    def __init__(self, settings: RetrievalSettings | None = None) -> None:
        self._settings: RetrievalSettings | None = None
        self._index: _IndexStore | None = None
        self._configuration_error: str | None = None
        self._closed = False
        try:
            self._settings = settings if settings is not None else RetrievalSettings()
        except Exception as exc:
            self._configuration_error = type(exc).__name__
            return
        self._index = _IndexStore(self._settings)

    def _require_settings(self) -> RetrievalSettings:
        if self._closed:
            raise RetrievalUnavailable("retrieval", "CLOSED")
        if self._settings is None:
            raise RetrievalUnavailable("configuration", self._configuration_error or "NOT_INITIALIZED")
        return self._settings

    def _require_index(self) -> _IndexStore:
        self._require_settings()
        state = self._index.probe()
        if state["status"] != "UP":
            raise RetrievalUnavailable("index", state["error"])
        return self._index

    def split(self, content: str, title: str) -> tuple[ChunkDraft, ...]:
        """Preserve the original body and UTF-8 offsets; document title is not prepended."""
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content must not be blank")
        if not isinstance(title, str):
            raise ValueError("title must be a string")
        try:
            content.encode("utf-8")
            title.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("content and title must be valid UTF-8") from None
        settings = self._require_settings()
        try:
            tokenizer = _load_chunk_tokenizer(str(settings.chunk_tokenizer_path))
        except Exception as exc:
            raise RetrievalUnavailable("tokenizer", type(exc).__name__) from None

        def count_tokens(text: str) -> int:
            try:
                return len(tokenizer.encode(text, add_special_tokens=True).ids)
            except Exception as exc:
                raise RetrievalUnavailable("tokenizer", type(exc).__name__) from None

        return tuple(split_markdown(
            content, count_tokens=count_tokens,
            max_tokens=settings.chunk_max_tokens, min_tokens=settings.chunk_min_tokens,
        ))

    def _embed(self, texts: list[str]) -> list[list[float]]:
        settings = self._require_settings()
        try:
            with _httpx.Client(timeout=settings.embedding_timeout_seconds) as client:
                return _embed_texts(client, texts, settings=settings)
        except Exception as exc:
            raise RetrievalUnavailable("embedding", type(exc).__name__) from None

    def replace(self, document_id: int, chunks: Sequence[IndexChunk]) -> int:
        _positive_id(document_id, "document_id")
        chunks = tuple(chunks)
        if not chunks:
            raise ValueError("chunks must not be empty")
        if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
            raise ValueError("chunk_id must be unique within a document")
        index = self._require_index()
        vectors = self._embed([chunk.embedding_text for chunk in chunks])
        try:
            return index.replace_document(document_id, chunks, vectors)
        except (ChunkIdConflict, IndexWriteError):
            raise
        except Exception as exc:
            raise RetrievalUnavailable("index", type(exc).__name__) from None

    def delete_document(self, document_id: int) -> int:
        _positive_id(document_id, "document_id")
        index = self._require_index()
        try:
            return index.delete_document(document_id)
        except Exception as exc:
            raise RetrievalUnavailable("index", type(exc).__name__) from None

    def search(
        self, query: str, top_k: int = 5,
        *, record_timing: Callable[[str, float], None] | None = None,
    ) -> tuple[SearchHit, ...]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must not be blank")
        if type(top_k) is not int or top_k <= 0:
            raise ValueError("top_k must be positive")
        with _measure("vector", record_timing):
            index = self._require_index()
        with _measure("embedding", record_timing):
            vectors = self._embed([query])
        with _measure("vector", record_timing):
            try:
                return index.query(
                    vectors[0], top_k=top_k,
                    lexical_query=query if self._settings.retrieval_strategy == "hybrid" else None,
                )
            except Exception as exc:
                raise RetrievalUnavailable("index", type(exc).__name__) from None

    def inspect(self) -> tuple[IndexEntry, ...]:
        index = self._require_index()
        try:
            return index.inspect()
        except Exception as exc:
            raise RetrievalUnavailable("index", type(exc).__name__) from None

    def reset(self) -> None:
        """Explicit maintenance can rebuild a collection rejected during initialization."""
        self._require_settings()
        try:
            self._index.reset()
        except Exception as exc:
            raise RetrievalUnavailable("index", type(exc).__name__) from None

    def runtime_info(self) -> dict:
        """Read local index state and public model metadata without network or tokenizer loading."""
        settings = self._settings
        embedding = {
            "provider": settings.embedding_provider if settings else None,
            "model": settings.embedding_model if settings else None,
            "dim": settings.embedding_dim if settings else None,
        }
        if self._closed or self._index is None:
            error = "CLOSED" if self._closed else self._configuration_error or "NOT_INITIALIZED"
            chroma = {"status": "DOWN", "error": error}
        else:
            chroma = self._index.probe()
        return {"status": chroma["status"], "embedding": embedding, "chroma": chroma,
                "strategy": settings.retrieval_strategy if settings else None}

    def health(self) -> dict:
        result = self.runtime_info()
        if self._closed or self._settings is None:
            error = result["chroma"]["error"]
            result["embedding"].update(status="DOWN", reachable=False, model_available=False, error=error)
            result["tokenizer"] = {"status": "DOWN", "error": error}
            return result
        result["embedding"] = _probe_embedding(self._settings)
        try:
            _load_chunk_tokenizer(str(self._settings.chunk_tokenizer_path))
            result["tokenizer"] = {"status": "UP"}
        except Exception as exc:
            result["tokenizer"] = {"status": "DOWN", "error": type(exc).__name__}
        result["status"] = "UP" if all(
            result[part]["status"] == "UP" for part in ("chroma", "embedding", "tokenizer")
        ) else "DOWN"
        return result

    def close(self) -> None:
        if self._closed:
            return
        if self._index is not None:
            try:
                self._index.close()
            except Exception as exc:
                raise RetrievalUnavailable("index", type(exc).__name__) from None
        self._closed = True
