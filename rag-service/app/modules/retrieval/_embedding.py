"""Native embedding protocol and immutable indexing inputs owned by retrieval."""

from __future__ import annotations

import math
from array import array
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from ._chunking import _embedding_text
from ._settings import RetrievalSettings


def _positive_id(value: int, name: str) -> None:
    if type(value) is not int or not 0 < value <= 2**63 - 1:
        raise ValueError(f"{name} must be a positive signed 64-bit integer")


@dataclass(frozen=True)
class IndexChunk:
    chunk_id: int
    text: str
    heading_path: str = ""
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _positive_id(self.chunk_id, "chunk_id")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("chunk text must not be blank")
        if not isinstance(self.heading_path, str):
            raise ValueError("heading_path must be a string")
        if not isinstance(self.tags, (list, tuple)) or any(not isinstance(tag, str) for tag in self.tags):
            raise ValueError("tags must contain strings")
        object.__setattr__(self, "tags", tuple(self.tags))

    @property
    def embedding_text(self) -> str:
        return _embedding_text(self.text, self.heading_path)


class EmbeddingResponseError(ValueError):
    """The response does not provide one valid finite vector per input."""


def embedding_url(settings: RetrievalSettings, endpoint: Literal["embeddings", "models"]) -> str:
    base = settings.embedding_base_url.rstrip("/")
    if settings.embedding_provider == "ollama":
        resource = "embed" if endpoint == "embeddings" else "tags"
        return f"{base}/api/{resource}"
    if not base.endswith("/v1"):
        base += "/v1"
    return f"{base}/{endpoint}"


def embedding_headers(settings: RetrievalSettings) -> dict[str, str]:
    api_key = settings.embedding_api_key.get_secret_value() if settings.embedding_api_key else ""
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _validated_vectors(
    payload: Any, *, provider: str, expected_count: int, dimensions: int,
) -> list[list[float]]:
    if not isinstance(payload, dict):
        raise EmbeddingResponseError("embedding response must be an object")
    if provider == "ollama":
        raw_vectors = payload.get("embeddings")
    else:
        entries = payload.get("data")
        if not isinstance(entries, list) or len(entries) != expected_count:
            raise EmbeddingResponseError("embedding count mismatch")
        if any(not isinstance(entry, dict) or type(entry.get("index")) is not int for entry in entries):
            raise EmbeddingResponseError("invalid embedding response index")
        if {entry["index"] for entry in entries} != set(range(expected_count)):
            raise EmbeddingResponseError("embedding response indices must match the input")
        raw_vectors = [entry.get("embedding") for entry in sorted(entries, key=lambda entry: entry["index"])]
    if not isinstance(raw_vectors, list) or len(raw_vectors) != expected_count:
        raise EmbeddingResponseError("embedding count mismatch")

    vectors = []
    for vector in raw_vectors:
        if not isinstance(vector, list) or len(vector) != dimensions:
            raise EmbeddingResponseError(f"embedding dimension mismatch: expected {dimensions}")
        if any(type(coordinate) not in (int, float) for coordinate in vector):
            raise EmbeddingResponseError("embedding coordinates must be numbers")
        try:
            converted = array("f", vector)
        except OverflowError as exc:
            raise EmbeddingResponseError("embedding coordinates are out of range") from exc
        if not all(math.isfinite(coordinate) for coordinate in converted):
            raise EmbeddingResponseError("embedding coordinates must be finite float32 values")
        vectors.append(converted.tolist())
    return vectors


def embed_texts(
    client: httpx.Client, texts: list[str], *, settings: RetrievalSettings,
) -> list[list[float]]:
    """Batch internally; fail immediately without truncating or retrying inputs."""
    vectors = []
    for offset in range(0, len(texts), settings.embed_batch_size):
        batch = texts[offset:offset + settings.embed_batch_size]
        payload = {"model": settings.embedding_model, "input": batch}
        if settings.embedding_provider == "ollama":
            payload["truncate"] = False
        else:
            payload["encoding_format"] = "float"
        response = client.post(
            embedding_url(settings, "embeddings"), json=payload,
            headers=embedding_headers(settings),
        )
        response.raise_for_status()
        try:
            response_payload = response.json()
        except ValueError as exc:
            raise EmbeddingResponseError("embedding response is not JSON") from exc
        vectors.extend(_validated_vectors(
            response_payload, provider=settings.embedding_provider,
            expected_count=len(batch), dimensions=settings.embedding_dim,
        ))
    return vectors


def _extract_model_names(provider: str, payload: Any) -> set[str]:
    if not isinstance(payload, dict):
        raise TypeError("model response must be an object")
    entries = payload.get("models") if provider == "ollama" else payload.get("data")
    if not isinstance(entries, list):
        raise TypeError("model list must be an array")
    key = "name" if provider == "ollama" else "id"
    names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise TypeError("model entry must be an object")
        value = entry.get(key)
        if isinstance(value, str):
            names.add(value)
    return names


def _model_present(configured: str, available: set[str]) -> bool:
    if configured in available:
        return True
    return any(name.split(":", 1)[0] == configured.split(":", 1)[0] for name in available if name)


def probe_embedding(settings: RetrievalSettings) -> dict[str, Any]:
    result: dict[str, Any] = {
        "provider": settings.embedding_provider,
        "model": settings.embedding_model,
        "dim": settings.embedding_dim,
    }
    try:
        with httpx.Client(timeout=3.0) as client:
            response = client.get(embedding_url(settings, "models"), headers=embedding_headers(settings))
        response.raise_for_status()
        result["reachable"] = True
    except Exception as exc:
        return {**result, "reachable": False, "model_available": False,
                "status": "DOWN", "error": type(exc).__name__}
    try:
        available = _extract_model_names(settings.embedding_provider, response.json())
    except Exception as exc:
        return {**result, "model_available": False, "status": "DOWN",
                "error": "BAD_RESPONSE", "error_detail": type(exc).__name__}
    result["model_available"] = _model_present(settings.embedding_model, available)
    result["status"] = "UP" if result["model_available"] else "DOWN"
    if not result["model_available"]:
        result["error"] = "MODEL_NOT_FOUND"
    return result
