"""RAG 服务配置。

设计约束（见 docs/子Issue-B-索引管线.md）：

* **模型与厂商可配置，不绑定 Ollama。** provider / model / dim 三元组从环境变量读，
  换厂商只改配置。
* **同时记录 embedding_model 与 embedding_dim。** 两者与索引元信息不一致时拒绝启动
  并提示重建——维度错配若不在此处拦住，报错会推迟到检索时才爆，且表现为距离
  计算异常，极难定位。
* **没有 JAVA_BASE_URL。** 推模式下 Python 不知道 Java 在哪，也不需要知道
  （B-6：全量重建由 Java 编排，Python 不反向调用）。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 索引持久化根目录：派生物，可从 MySQL 全量重建，不入库（.gitignore 已挡 /data/）
SERVICE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = SERVICE_DIR / "data"


class Settings(BaseSettings):
    """从环境变量 / .env 读取的服务配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    # ---- embedding ----
    embedding_provider: Literal["ollama", "openai", "deepseek"] = "ollama"
    embedding_model: str = "bge-m3"
    embedding_dim: int = 1024
    embedding_base_url: str = "http://localhost:11434"
    embedding_api_key: SecretStr | None = None
    embedding_timeout_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)

    # ---- 切片 ----
    chunk_max_tokens: int = Field(default=512, gt=0)
    chunk_min_tokens: int = Field(default=64, ge=0)
    chunk_tokenizer_path: Path = (
        SERVICE_DIR / "data/tokenizers/bge-m3-5617a9f61b028005a4858fdac845db406aefb181.json"
    )

    # ---- 批量 ----
    # Python 内部批量大小；一次 /embed 接收一篇文档的完整 chunks。
    embed_batch_size: int = Field(default=64, ge=1)

    @field_validator("chunk_tokenizer_path")
    @classmethod
    def _tokenizer_path_is_service_relative(cls, value: Path) -> Path:
        return value if value.is_absolute() else SERVICE_DIR / value

    @model_validator(mode="after")
    def _chunk_limits_must_be_ordered(self) -> Settings:
        if self.chunk_min_tokens > self.chunk_max_tokens:
            raise ValueError("CHUNK_MIN_TOKENS 不能大于 CHUNK_MAX_TOKENS")
        return self

    @field_validator("embedding_dim")
    @classmethod
    def _dim_must_be_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("EMBEDDING_DIM 必须为正整数")
        return v

    @property
    def collection_name(self) -> str:
        """Chroma collection 名：`easyrag_<模型>_<维度>`。

        两件事由这个名字保证：

        * **换模型即换 collection。** 旧向量与新向量不可比，混在一个
          collection 里检索会静默劣化，所以把"换模型"变成显式动作。
        * **换维度也换 collection。** 同一模型改成截断输出（如 bge-m3
          从 1024 降到 512）时模型名不变，若只按模型名命名，旧 collection
          会被原样复用且无人拦截——错配会推迟到检索时才爆，表现为距离
          计算异常，极难定位。把 dim 编进名字，不一致天然变成"换 collection"。

        名字里的非法字符会被替换：Chroma 只接受 `[a-zA-Z0-9._-]`，而
        `bge-m3:latest`（Ollama 的默认标签写法）和 `Qwen/Qwen3-Embedding-0.6B`
        （HF 风格，vLLM / Xinference 等 openai 兼容端点常见）都是合法配置值，
        不净化会让进程在建 collection 时直接起不来。
        """
        safe_model = re.sub(r"[^a-zA-Z0-9._-]", "-", self.embedding_model)
        return f"easyrag_{safe_model}_{self.embedding_dim}"

    @property
    def chroma_dir(self) -> Path:
        return DATA_DIR / "chroma"


class LlmSettings(BaseSettings):
    """按需读取的 LLM 配置，不参与索引服务的启动检查。"""

    model_config = SettingsConfigDict(
        env_prefix="LLM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    provider: Literal["openai", "deepseek"] = "openai"
    model: str = Field(min_length=1)
    base_url: AnyHttpUrl | None = None
    api_key: SecretStr = Field(min_length=1)
    timeout_seconds: float = Field(default=60.0, gt=0)


@lru_cache
def get_settings() -> Settings:
    """进程内单例。配置在启动时读一次，运行中不重载。"""
    return Settings()
