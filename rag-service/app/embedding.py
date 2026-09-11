"""整篇索引使用的 chunk 输入与原生 embedding HTTP 客户端。"""

from __future__ import annotations

import math
from array import array
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, field_validator

from app.config import Settings


class EmbeddingChunk(BaseModel):
    chunk_id: int = Field(strict=True, gt=0, le=2**63 - 1)
    text: str = Field(min_length=1)
    heading_path: str
    tags: list[str] = Field(default_factory=list)

    @field_validator("text")
    @classmethod
    def _text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value

    @property
    def embedding_text(self) -> str:
        return f"{self.text}\n{self.heading_path}" if self.heading_path else self.text


class EmbeddingResponseError(ValueError):
    """模型响应无法与输入逐条对应，或向量不满足配置要求。"""


def embedding_url(settings: Settings, endpoint: Literal["embeddings", "models"]) -> str:
    base = settings.embedding_base_url.rstrip("/")
    if settings.embedding_provider == "ollama":
        resource = "embed" if endpoint == "embeddings" else "tags"
        return f"{base}/api/{resource}"
    if not base.endswith("/v1"):
        base += "/v1"
    return f"{base}/{endpoint}"


def embedding_headers(settings: Settings) -> dict[str, str]:
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
    client: httpx.Client, texts: list[str], *, settings: Settings,
) -> list[list[float]]:
    """按配置内部批处理；失败立即退出，不截断输入、不自动重试。"""
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
