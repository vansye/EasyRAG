"""问答管线测试：LLM 用假模型（返回预制文本），Chroma 用 tmp_path，完全离线。"""

from __future__ import annotations

import os

import httpx
import pytest

from app import config
from app.config import LlmSettings, Settings
from app.index_store import IndexStore
from app.qa import QaError, QaPipeline, QaResponse
from app.retrieval import RetrievedChunk


@pytest.fixture
def settings(tmp_path, monkeypatch):
    for variable in list(os.environ):
        if variable.startswith(("EMBEDDING_", "CHUNK_", "EMBED_", "LLM_")):
            monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return Settings(_env_file=None, embedding_model="test-embedding", embedding_dim=3)


class _ScriptedModel:
    """按调用顺序返回预制内容的假 LLM：第一次是判定，其后是生成。"""

    def __init__(self, judge_reply: str, generate_reply: str):
        self.judge_reply = judge_reply
        self.generate_reply = generate_reply
        self.prompts: list[str] = []

    def invoke(self, messages):
        self.prompts.append(messages[0].content)
        if len(self.prompts) == 1:
            return type("Reply", (), {"content": self.judge_reply})()
        return type("Reply", (), {"content": self.generate_reply})()


class _FixedVectorTransport(httpx.BaseTransport):
    def __init__(self, vector):
        self.vector = vector

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [self.vector]})


def _fake_embedding_vector(monkeypatch, vector):
    """让 retrieve 内部自建的 httpx.Client 走假 transport，问题向量固定。"""
    original_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: original_client(
        transport=_FixedVectorTransport(vector)))


def _pipeline(settings: Settings, model, index=None) -> QaPipeline:
    return QaPipeline(
        settings=settings,
        index=index if index is not None else IndexStore(settings),
        llm_settings=None,
        model=model,
    )


CHUNKS = [
    RetrievedChunk(chunk_id=101, document_id=11, text="ACID 指原子性、一致性、隔离性、持久性。", heading_path="", score=0.82),
    RetrievedChunk(chunk_id=102, document_id=12, text="Redis 事务不保证原子性。", heading_path="", score=0.61),
]


class TestJudge:
    def test_parses_verdict_from_json(self, settings):
        pipeline = _pipeline(settings, _ScriptedModel('{"verdict": "SUFFICIENT"}', ""))
        assert pipeline.judge("问题", CHUNKS) == "SUFFICIENT"
        # 判定 prompt 必须含片段与三条判定规则
        prompt = pipeline.model.prompts[0]
        assert "[1] ACID" in prompt
        assert "SUFFICIENT" in prompt and "PARTIAL" in prompt and "NONE" in prompt

    @pytest.mark.parametrize("reply", ["好的，可以回答", '{"verdict": "MAYBE"}', ""])
    def test_unparseable_output_is_qa_error_not_refusal(self, settings, reply):
        pipeline = _pipeline(settings, _ScriptedModel(reply, ""))
        with pytest.raises(QaError):
            pipeline.judge("问题", CHUNKS)

    def test_generate_instructs_citation_and_no_fabrication(self, settings):
        pipeline = _pipeline(settings, _ScriptedModel('{"verdict": "SUFFICIENT"}', "答 [1]"))
        pipeline.generate("问题", CHUNKS, partial=False)
        prompt = pipeline.model.prompts[0]
        assert "[n]" in prompt
        assert "不得编造" in prompt
        assert "[1]" in prompt and "[2]" in prompt

    def test_generate_partial_adds_boundary_instruction(self, settings):
        pipeline = _pipeline(settings, _ScriptedModel('{"verdict": "PARTIAL"}', "部分答"))
        pipeline.generate("问题", CHUNKS, partial=True)
        assert "覆盖边界" in pipeline.model.prompts[0]


class TestAnswerQuestion:
    def _index_with_hit(self, settings):
        index = IndexStore(settings)
        index._collection.add(
            ids=["101"], embeddings=[[1.0, 0.0, 0.0]],
            documents=["ACID 指原子性。"], metadatas=[{"document_id": 11, "heading_path": ""}],
        )
        return index

    def test_sufficient_path_returns_answer_with_citations_and_trace(self, settings, monkeypatch):
        index = self._index_with_hit(settings)
        _fake_embedding_vector(monkeypatch, [1.0, 0.0, 0.0])
        pipeline = QaPipeline(settings=settings, index=index,
                              model=_ScriptedModel('{"verdict": "SUFFICIENT"}', "ACID 是四个特性 [1]。"))

        response = pipeline.answer_question("什么是 ACID？")

        assert response.status == "ANSWERED"
        assert response.answer == "ACID 是四个特性 [1]。"
        assert response.chunk_ids == [101]
        assert len(response.trace) == 1
        entry = response.trace[0]
        assert entry.round_index == 1
        assert entry.decision == "SUFFICIENT"
        assert entry.retrieved[0]["chunk_id"] == 101
        assert entry.query == "什么是 ACID？"

    def test_none_path_refuses_without_generation(self, settings, monkeypatch):
        index = IndexStore(settings)  # 空索引必然检索不到
        model = _ScriptedModel('{"verdict": "NONE"}', "不应该被调用的生成")
        _fake_embedding_vector(monkeypatch, [1.0, 0.0, 0.0])
        pipeline = QaPipeline(settings=settings, index=index, model=model)

        response = pipeline.answer_question("Redis 的 GEO 命令怎么用？")

        assert response.status == "REFUSED"
        assert response.chunk_ids == []
        # 不变量 4：拒答不进入生成——模型只被调用了一次（判定）
        assert len(model.prompts) == 1
        assert "知识库中没有找到" in response.answer

    def test_partial_path_returns_boundary_answer(self, settings, monkeypatch):
        index = self._index_with_hit(settings)
        _fake_embedding_vector(monkeypatch, [1.0, 0.0, 0.0])
        pipeline = QaPipeline(settings=settings, index=index,
                              model=_ScriptedModel('{"verdict": "PARTIAL"}', "库中仅提及……"))

        response = pipeline.answer_question("什么是 Transformer 的多头注意力？")

        assert response.status == "PARTIAL"
        assert response.chunk_ids == [101]

    def test_blank_question_rejected(self, settings):
        with pytest.raises(ValueError, match="blank"):
            _pipeline(settings, _ScriptedModel("", "")).answer_question("  ")

    def test_llm_empty_content_is_qa_error(self, settings, monkeypatch):
        model = type("M", (), {"invoke": staticmethod(lambda m: type("R", (), {"content": ""})())})()
        _fake_embedding_vector(monkeypatch, [1.0, 0.0, 0.0])
        pipeline = QaPipeline(settings=settings, index=IndexStore(settings), model=model)
        with pytest.raises(QaError):
            pipeline.answer_question("问题")

    def test_llm_upstream_exception_becomes_qa_error(self, settings, monkeypatch):
        """LangChain 的异常族（超时、连接失败）必须收口成 QaError。

        端到端实测发现：本地模型生成超时抛 OpenAITimeoutError，它既不是
        httpx 异常也不是 QaError，会逃逸 /query 的 handler 变成裸 500——
        调用方分不出"模型超时"和"服务崩了"。
        """
        class _TimingOut:
            @staticmethod
            def invoke(messages):
                raise RuntimeError("Request timed out.")

        _fake_embedding_vector(monkeypatch, [1.0, 0.0, 0.0])
        pipeline = QaPipeline(settings=settings, index=IndexStore(settings), model=_TimingOut())
        with pytest.raises(QaError, match="RuntimeError"):
            pipeline.answer_question("问题")
