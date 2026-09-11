"""两份派生索引的持有者：Chroma（向量）与 BM25（词法）。

M1 建立"能报告自身状态"的最小形态；M2 逐步接入索引维护，
当前支持整篇替换、按文档删除与清空当前 collection。两条边界性事实不变：

1. **BM25 空启动。** 进程起来时词法索引是 0 条，不向任何人拉数据。
   Python 启动零外部依赖，避免与 Java 互等（B-7）。
2. **索引是派生物。** 删掉 data/chroma/ 再重启，不丢任何真相，
   代价只是重建时间（架构不变量二）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.config import Settings
from app.embedding import EmbeddingChunk


@dataclass
class Bm25Index:
    """词法索引（内存态）。

    M1 阶段只持有语料容器与计数。真正的 jieba 分词与检索在 M2/M4 接入——
    M2 只用向量检索，BM25 空着不影响；M4 混合检索（RRF 融合）时启用。

    现在就建的理由（B-5）：让"更新同步"从一开始就同时覆盖两份索引，
    避免 M4 再补一套 BM25 的失效逻辑。
    """

    chunk_ids: list[int] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.chunk_ids)


class IndexMetadataMismatch(RuntimeError):
    """索引元信息与当前配置不符。

    抛它就是拒绝启动——这是文档承诺的"维度守门"的落地：宁可现在起不来
    并说清原因，也不要带着错配的索引进入检索期。
    """


class ChunkIdConflict(ValueError):
    """请求中的 chunk_id 已被当前 collection 中的另一篇文档占用。"""


class IndexWriteError(RuntimeError):
    """写入失败，附带本次清理是否也失败；仅保留异常类型。"""

    def __init__(self, cause: str, cleanup_error: str | None) -> None:
        super().__init__("index write failed")
        self.cause = cause
        self.cleanup_error = cleanup_error


class IndexStore:
    """Chroma + BM25 的门面。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._write_lock = Lock()
        self.bm25 = Bm25Index()
        self.chroma_error: str | None = None
        self._collection = None

        try:
            settings.chroma_dir.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(
                path=str(settings.chroma_dir),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            self._collection = self._client.get_or_create_collection(
                name=settings.collection_name,
                metadata={
                    # bge-m3 官方建议 cosine（B-9）。写死起步，异常再议。
                    "hnsw:space": "cosine",
                    # 记下产出这批向量的模型与维度，供下面比对。
                    "embedding_model": settings.embedding_model,
                    "embedding_dim": settings.embedding_dim,
                },
            )
            self._assert_metadata_matches()
        except IndexMetadataMismatch:
            # 元信息不符是配置错误，必须让进程起不来（守门的意义所在）
            raise
        except Exception as exc:  # noqa: BLE001
            # 目录不可读、sqlite 损坏等：进程仍要起得来，由 /health 如实报 DOWN。
            # 与 Java 侧对称——"进程活着"是独立于依赖的事实。
            self.chroma_error = type(exc).__name__

    def _assert_metadata_matches(self) -> None:
        """比对 collection 元信息与当前配置。

        collection 名已含模型与维度，所以正常情况下不可能不符；但已存在的
        旧 collection（早期版本建的，或手工改过 metadata）可能带着不同的
        模型/维度。这里显式比对，把隐性事故变成一句可读的启动错误。
        """
        meta = (self._collection.metadata or {}) if self._collection else {}
        recorded_model = meta.get("embedding_model")
        recorded_dim = meta.get("embedding_dim")

        if recorded_model is not None and recorded_model != self._settings.embedding_model:
            raise IndexMetadataMismatch(
                f"collection {self._settings.collection_name!r} 由模型 "
                f"{recorded_model!r} 建立，当前配置为 {self._settings.embedding_model!r}。"
                "换模型需全量重建：删除该 collection 或改用新的模型名后由 Java 触发重建。"
            )
        if recorded_dim is not None and int(recorded_dim) != self._settings.embedding_dim:
            raise IndexMetadataMismatch(
                f"collection {self._settings.collection_name!r} 的向量维度为 "
                f"{recorded_dim}，当前配置 EMBEDDING_DIM={self._settings.embedding_dim}。"
                "维度错配会让检索结果静默劣化，须全量重建。"
            )

    @property
    def collection_name(self) -> str:
        return self._settings.collection_name

    def vector_count(self) -> int:
        if self._collection is None:
            raise RuntimeError(self.chroma_error or "collection 未初始化")
        return self._collection.count()

    def delete_document(self, document_id: int) -> int:
        """按文档删除当前 collection 中的向量，返回实际删除条数。"""
        with self._write_lock:
            if self._collection is None:
                raise RuntimeError(self.chroma_error or "collection 未初始化")
            result = self._collection.delete(where={"document_id": document_id})
            return result["deleted"]

    def reset(self) -> None:
        """清空当前 collection，保留其元信息及其他模型的 collection。"""
        with self._write_lock:
            if self._collection is None:
                raise RuntimeError(self.chroma_error or "collection 未初始化")
            chunk_ids = self._collection.get(include=[])["ids"]
            if chunk_ids:
                self._collection.delete(ids=chunk_ids)
            self.bm25.chunk_ids.clear()

    def replace_document(
        self, document_id: int, chunks: list[EmbeddingChunk], vectors: list[list[float]],
    ) -> int:
        """接收整篇已校验向量，先清旧后写新；写入失败则尝试清理，不承诺原子性。"""
        with self._write_lock:
            if self._collection is None:
                raise RuntimeError(self.chroma_error or "collection 未初始化")
            identifiers = [str(chunk.chunk_id) for chunk in chunks]
            existing = self._collection.get(ids=identifiers, include=["metadatas"])
            if any(not metadata or metadata.get("document_id") != document_id
                   for metadata in existing["metadatas"]):
                raise ChunkIdConflict("chunk_id belongs to another document")
            batch_size = min(self._settings.embed_batch_size, self._client.get_max_batch_size())
            documents = [chunk.embedding_text for chunk in chunks]
            metadatas = [
                {"document_id": document_id, "seq": sequence, "heading_path": chunk.heading_path,
                 **({"tags": chunk.tags} if chunk.tags else {})}
                for sequence, chunk in enumerate(chunks)
            ]
            try:
                self._collection.delete(where={"document_id": document_id})
                for offset in range(0, len(chunks), batch_size):
                    self._collection.upsert(
                        ids=identifiers[offset:offset + batch_size],
                        embeddings=vectors[offset:offset + batch_size],
                        documents=documents[offset:offset + batch_size],
                        metadatas=metadatas[offset:offset + batch_size],
                    )
            except Exception as exc:
                cleanup_error = None
                try:
                    self._collection.delete(where={"document_id": document_id})
                except Exception as cleanup_exc:
                    cleanup_error = type(cleanup_exc).__name__
                raise IndexWriteError(type(exc).__name__, cleanup_error) from exc
            return len(chunks)

    def probe(self) -> dict[str, Any]:
        """向量索引的可读写探针，供 /health 使用。

        两类失败都要如实报告：初始化就失败（目录损坏，`chroma_error` 已记下）
        与运行期读取失败。
        """
        if self._collection is None:
            return {
                "status": "DOWN",
                "collection": self.collection_name,
                "error": self.chroma_error or "NOT_INITIALIZED",
            }
        try:
            return {
                "status": "UP",
                "collection": self.collection_name,
                "vectors": self.vector_count(),
            }
        except Exception as exc:  # noqa: BLE001 —— 健康检查要如实报告任何失败
            return {
                "status": "DOWN",
                "collection": self.collection_name,
                "error": type(exc).__name__,
            }
