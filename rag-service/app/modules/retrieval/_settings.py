"""Embedding, tokenizer, and derived-index settings owned by retrieval."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


SERVICE_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = SERVICE_DIR / "data"
# The default tokenizer is pinned to one Hugging Face commit so chunk boundaries stay reproducible.
DEFAULT_TOKENIZER_REPO = "BAAI/bge-m3"
DEFAULT_TOKENIZER_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
DEFAULT_TOKENIZER_PATH = DATA_DIR / f"tokenizers/bge-m3-{DEFAULT_TOKENIZER_REVISION}.json"


class RetrievalSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=SERVICE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    embedding_provider: Literal["ollama", "openai", "deepseek"] = "ollama"
    embedding_model: str = "bge-m3"
    embedding_dim: int = 1024
    embedding_base_url: str = "http://localhost:11434"
    embedding_api_key: SecretStr | None = None
    embedding_timeout_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)

    chunk_max_tokens: int = Field(default=512, gt=0)
    chunk_min_tokens: int = Field(default=64, ge=0)
    chunk_tokenizer_path: Path = DEFAULT_TOKENIZER_PATH
    embed_batch_size: int = Field(default=64, ge=1)
    chroma_dir: Path = DATA_DIR / "chroma"
    # "hybrid" fuses BM25 over the same payload with the vector ranking (B-17); "dense" is the vector ranking alone.
    retrieval_strategy: Literal["dense", "hybrid"] = "hybrid"
    retrieval_candidates: int = Field(default=20, ge=1)

    @field_validator("chunk_tokenizer_path", "chroma_dir")
    @classmethod
    def _paths_are_service_relative(cls, value: Path) -> Path:
        return value if value.is_absolute() else SERVICE_DIR / value

    @model_validator(mode="after")
    def _chunk_limits_must_be_ordered(self) -> RetrievalSettings:
        if self.chunk_min_tokens > self.chunk_max_tokens:
            raise ValueError("CHUNK_MIN_TOKENS 不能大于 CHUNK_MAX_TOKENS")
        return self

    @field_validator("embedding_dim")
    @classmethod
    def _dim_must_be_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("EMBEDDING_DIM 必须为正整数")
        return value

    @property
    def collection_name(self) -> str:
        safe_model = re.sub(r"[^a-zA-Z0-9._-]", "-", self.embedding_model)
        return f"easyrag_{safe_model}_{self.embedding_dim}"
