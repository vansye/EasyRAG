"""RAG 服务入口（模块 B：索引管线）。

M1 提供 /health；M2 已接入切片、整篇 embedding、按文档删除与重置。

边界（子 Issue B §四）：本服务不持有业务真相，不读写 MySQL，
不反向调用 Java。它只被动接受 Java 的调用。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict
from functools import lru_cache
from typing import Annotated, Any, Self

import httpx
import pydantic
from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, Field, field_validator, model_validator
from tokenizers import Tokenizer

from app.chunking import split_markdown
from app.config import Settings, get_settings
from app.embedding import EmbeddingChunk, EmbeddingResponseError, embed_texts, embedding_headers, embedding_url
from app.index_store import ChunkIdConflict, IndexStore, IndexWriteError
from app.model_config import ModelConfigUnavailable
from app.qa import QaError, QaPipeline, QaRequest, QaResponse
from app.runtime import router as runtime_router

SERVICE_NAME = "easyrag-rag-service"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时建立索引门面。

    注意这里**不**做任何外部拉取：BM25 空启动、Chroma 读本地目录。
    进程可用性不依赖 Java 或 embedding 服务是否在线——它们的状态由
    /health 如实报告，而不是让本进程起不来（B-7）。
    """
    settings = get_settings()
    app.state.settings = settings
    app.state.index = IndexStore(settings)
    yield


app = FastAPI(
    title="EasyRAG RAG Service",
    description="索引管线：切片、embedding、向量与词法索引（模块 B）",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(runtime_router)


async def _probe_embedding(settings: Settings) -> dict[str, Any]:
    """探测 embedding 端点，并校验**配置的模型确实存在**。

    Java 侧据此判定 FAILED 与是否可以开始全量重建，所以这里必须如实反映
    "配置的模型是否真的能用"。

    只探端点可达是不够的：Ollama 在线但没拉 bge-m3 时，端点照常 200，
    健康检查会绿着，直到 M2 第一次 embed 才炸——绿的健康检查配上坏掉的
    现实，是最难查的一类故障（Java 侧 Flyway 缺自动配置时也栽在这里：
    /health 报 DB UP，但一张表都没建）。
    """
    result: dict[str, Any] = {
        "provider": settings.embedding_provider,
        "model": settings.embedding_model,
        "dim": settings.embedding_dim,
    }
    url = embedding_url(settings, "models")
    headers = embedding_headers(settings)

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(url, **({"headers": headers} if headers else {}))
        response.raise_for_status()
        result["reachable"] = True
    except Exception as exc:  # noqa: BLE001 —— 不可达是正常状态，不是异常
        result["reachable"] = False
        result["model_available"] = False
        result["status"] = "DOWN"
        result["error"] = type(exc).__name__
        return result

    # 解析也必须在 try 内：上游"200 但不是预期 JSON"是最容易发生的错配
    # （EMBEDDING_BASE_URL 填成了 web 服务器根路径、或走了带门户页的反代），
    # 若让异常逃逸，/health 会返回 500，Java 就会把它归因成"Python 挂了"——
    # 而分层健康检查存在的全部理由正是防止这种误判。
    try:
        available = _extract_model_names(settings.embedding_provider, response.json())
    except Exception as exc:  # noqa: BLE001
        result["model_available"] = False
        result["status"] = "DOWN"
        result["error"] = "BAD_RESPONSE"
        result["error_detail"] = type(exc).__name__
        return result

    result["model_available"] = _model_present(settings.embedding_model, available)
    # 端点可达但模型不在 = DOWN。宁可现在红，也不要 M2 时才炸。
    result["status"] = "UP" if result["model_available"] else "DOWN"
    if not result["model_available"]:
        result["error"] = "MODEL_NOT_FOUND"
        result["available_models"] = sorted(available)
    return result


def _extract_model_names(provider: str, payload: Any) -> set[str]:
    """从厂商各自的响应结构里取出模型名集合。

    对结构做防御：上游可能返回合法 JSON 但不是预期形状（`[]`、
    `{"models": null}`、元素不是对象）。这里宁可返回空集让调用方判 DOWN，
    也不能抛异常——见 `_probe_embedding` 里的解析 try。
    """
    if not isinstance(payload, dict):
        raise TypeError(f"响应不是对象: {type(payload).__name__}")

    entries = payload.get("models") if provider == "ollama" else payload.get("data")
    if not isinstance(entries, list):
        raise TypeError(f"模型列表字段不是数组: {type(entries).__name__}")

    key = "name" if provider == "ollama" else "id"
    names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            # 元素不是对象 = 响应结构不对，不能当成"模型不存在"：
            # 后者会让人去 pull 模型，而真正的问题是端点回错了东西。
            raise TypeError(f"模型条目不是对象: {type(entry).__name__}")
        value = entry.get(key)
        if isinstance(value, str):
            names.add(value)
    return names


def _model_present(configured: str, available: set[str]) -> bool:
    """判断配置的模型是否在可用列表里。

    Ollama 的模型名默认带 `:latest` 标签（配置里写 `bge-m3`，
    列表里是 `bge-m3:latest`），两种写法都算命中。
    """
    if configured in available:
        return True
    return any(name.split(":", 1)[0] == configured.split(":", 1)[0] for name in available if name)


@app.get("/health")
async def health() -> dict[str, Any]:
    """服务健康检查。

    分层报告的理由与 Java 侧一致：进程活着是独立于依赖的事实。
    embedding 端点挂掉时本服务照常返回 200，只把 embedding 标成 DOWN——
    否则 Java 无法区分"Python 挂了"和"模型服务挂了"，FAILED 的归因会错。
    """
    settings: Settings = app.state.settings
    index: IndexStore = app.state.index

    return {
        "status": "UP",
        "service": SERVICE_NAME,
        "chroma": index.probe(),
        "embedding": await _probe_embedding(settings),
        "bm25": {"chunks": index.bm25.size},
    }


@app.delete("/index/{document_id}", responses={503: {"description": "向量索引不可用"}})
def delete_index(document_id: Annotated[int, Path(gt=0, le=2**63 - 1)]) -> dict[str, int]:
    """删除指定文档的向量索引，重复调用返回 removed = 0。"""
    index: IndexStore = app.state.index
    try:
        removed = index.delete_document(document_id)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "INDEX_UNAVAILABLE", "cause": type(exc).__name__},
        ) from exc
    return {"removed": removed}


@app.post("/reset", responses={503: {"description": "向量索引不可用"}})
def reset_index() -> dict[str, bool]:
    """清空当前 collection，不触碰其他模型的对照索引。"""
    index: IndexStore = app.state.index
    try:
        index.reset()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "INDEX_UNAVAILABLE", "cause": type(exc).__name__},
        ) from exc
    return {"reset": True}


class EmbedRequest(BaseModel):
    document_id: int = Field(strict=True, gt=0, le=2**63 - 1)
    chunks: list[EmbeddingChunk] = Field(min_length=1)

    @model_validator(mode="after")
    def _chunk_ids_must_be_unique(self) -> Self:
        if len({chunk.chunk_id for chunk in self.chunks}) != len(self.chunks):
            raise ValueError("chunk_id must be unique within a document")
        return self


@app.post("/embed", responses={
    409: {"description": "chunk_id 已属于另一篇文档"},
    503: {"description": "模型或索引不可用；写入失败时附清理结果"},
})
def embed_document(payload: EmbedRequest) -> dict[str, int]:
    """接收按 seq 排序的整篇 chunks，在 Python 内部分批生成向量并替换索引。"""
    settings: Settings = app.state.settings
    index: IndexStore = app.state.index
    try:
        index.vector_count()
    except Exception as exc:
        raise HTTPException(status_code=503, detail={
            "error": "INDEX_UNAVAILABLE", "cause": type(exc).__name__,
        }) from exc
    try:
        with httpx.Client(timeout=settings.embedding_timeout_seconds) as client:
            vectors = embed_texts(client, [chunk.embedding_text for chunk in payload.chunks], settings=settings)
    except (httpx.HTTPError, EmbeddingResponseError) as exc:
        raise HTTPException(status_code=503, detail={
            "error": "EMBEDDING_UNAVAILABLE", "cause": type(exc).__name__,
        }) from exc
    try:
        indexed = index.replace_document(payload.document_id, payload.chunks, vectors)
    except ChunkIdConflict as exc:
        raise HTTPException(status_code=409, detail={"error": "CHUNK_ID_CONFLICT"}) from exc
    except IndexWriteError as exc:
        raise HTTPException(status_code=503, detail={
            "error": "INDEX_WRITE_FAILED", "cause": exc.cause, "cleanup_error": exc.cleanup_error,
        }) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail={
            "error": "INDEX_UNAVAILABLE", "cause": type(exc).__name__,
        }) from exc
    return {"indexed": indexed}


@app.post("/query", responses={
    503: {"description": "embedding、索引或 LLM 不可用"},
})
def answer_question(payload: QaRequest) -> QaResponse:
    """模块 C 的问答入口（仅 Java 调用，不暴露给前端）。

    检索 → 三态判定 → 生成/拒答，单轮。拒答是正常返回（status=REFUSED），
    不是错误；503 只用于技术故障（embedding 索引不可达、判定输出无法解析）。
    """
    settings: Settings = app.state.settings
    index: IndexStore = app.state.index
    try:
        index.vector_count()
    except Exception as exc:
        raise HTTPException(status_code=503, detail={
            "error": "INDEX_UNAVAILABLE", "cause": type(exc).__name__,
        }) from exc
    try:
        pipeline = QaPipeline(settings=settings, index=index)
        return pipeline.answer_question(payload.question.strip())
    except QaError as exc:
        raise HTTPException(status_code=503, detail={
            "error": "LLM_UNAVAILABLE", "cause": "QA_PIPELINE",
            "detail": str(exc)[:200],
        }) from exc
    except ModelConfigUnavailable as exc:
        raise HTTPException(status_code=503, detail={
            "error": "LLM_CONFIG_UNAVAILABLE", "cause": "ModelConfigUnavailable",
        }) from exc
    except pydantic.ValidationError as exc:
        # LlmSettings 缺必填项（未配置 LLM_ 环境变量）：这是部署配置缺失，
        # 不是运行期故障。给出可操作的错误码而不是裸 500——Java 侧与运维
        # 都需要能区分"模型没配"和"模型挂了"。
        raise HTTPException(status_code=503, detail={
            "error": "LLM_NOT_CONFIGURED", "cause": "ValidationError",
        }) from exc
    except (httpx.HTTPError, EmbeddingResponseError) as exc:
        raise HTTPException(status_code=503, detail={
            "error": "EMBEDDING_UNAVAILABLE", "cause": type(exc).__name__,
        }) from exc


class ChunkRequest(BaseModel):
    document_id: int = Field(strict=True, gt=0, le=2**63 - 1)
    text: str = Field(min_length=1)
    title: str

    @field_validator("text", "title", mode="before")
    @classmethod
    def _strings_must_be_utf8(cls, value: Any) -> Any:
        if isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise HTTPException(status_code=422, detail={"error": "INVALID_UTF8_TEXT"}) from exc
        return value

    @field_validator("text")
    @classmethod
    def _text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value


class ChunkResult(BaseModel):
    seq: int
    text: str
    byte_start: int = Field(description="原文 UTF-8 字节起始偏移，包含此位置")
    byte_end: int = Field(description="原文 UTF-8 字节结束偏移，不包含此位置")
    heading_path: str
    token_count: int = Field(description="正文、非空标题路径及模型特殊 token 的总计数")


@lru_cache(maxsize=1)
def _load_chunk_tokenizer(path: str) -> Tokenizer:
    """懒加载本地 tokenizer；同路径替换文件后须重启服务。"""
    tokenizer = Tokenizer.from_file(path)
    tokenizer.no_truncation()
    tokenizer.no_padding()
    return tokenizer


@app.post("/chunk", responses={
    422: {"description": "输入无效，或单个字符加标题路径已超过 token 预算"},
    503: {"description": "本地 tokenizer 不可用"},
})
def chunk_document(payload: ChunkRequest) -> dict[str, list[ChunkResult]]:
    """正文不规范化；只返回 UTF-8 字节偏移，不创建 chunk ID 或修改索引。"""
    settings: Settings = app.state.settings
    try:
        tokenizer = _load_chunk_tokenizer(str(settings.chunk_tokenizer_path))
    except Exception as exc:
        raise HTTPException(status_code=503, detail={
            "error": "TOKENIZER_UNAVAILABLE", "cause": type(exc).__name__,
        }) from exc

    def count_tokens(text: str) -> int:
        try:
            return len(tokenizer.encode(text, add_special_tokens=True).ids)
        except Exception as exc:
            raise HTTPException(status_code=503, detail={
                "error": "TOKENIZER_UNAVAILABLE", "cause": type(exc).__name__,
            }) from exc

    try:
        chunks = split_markdown(
            payload.text, count_tokens=count_tokens,
            max_tokens=settings.chunk_max_tokens, min_tokens=settings.chunk_min_tokens,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error": "CHUNK_TOKEN_LIMIT_EXCEEDED"}) from exc
    return {"chunks": [ChunkResult(seq=sequence, **asdict(chunk)) for sequence, chunk in enumerate(chunks)]}
