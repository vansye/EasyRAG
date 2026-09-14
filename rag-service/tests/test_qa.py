"""问答管线测试：LLM 用假模型（返回预制文本），Chroma 用 tmp_path，完全离线。"""

from __future__ import annotations

import os

import httpx
import httpx2
import pytest
from fastapi.testclient import TestClient

from app import config, main, qa
from app.config import LlmSettings, Settings
from app.index_store import IndexStore
from app.llm import create_chat_model
from app.qa import QaError, QaPipeline, QaResponse
from app.retrieval import RetrievedChunk


@pytest.fixture
def settings(tmp_path, monkeypatch):
    for variable in list(os.environ):
        if variable.startswith(("EMBEDDING_", "CHUNK_", "EMBED_", "LLM_")):
            monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
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

@pytest.mark.parametrize("provider", ["openai", "deepseek"])
@pytest.mark.parametrize("failing_stage", ["judge", "generate"])
@pytest.mark.parametrize("failure_kind", ["timeout", "api_error"])
def test_sdk_failure_keeps_existing_llm_unavailable_response(
    settings, monkeypatch, provider, failing_stage, failure_kind,
):
    requests = []
    private_details = "private-question Authorization: Bearer private-api-key"
    original_send = httpx2.Client.send

    def send(_client, request, **_options):
        if request.url.host == "testserver":
            return original_send(_client, request, **_options)
        assert request.url.host == "private-model.example.test"
        requests.append(request)
        if failing_stage == "generate" and len(requests) == 1:
            return httpx2.Response(200, request=request, json={
                "id": "judge-result",
                "object": "chat.completion",
                "created": 0,
                "model": "test-model",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": '{"verdict": "SUFFICIENT"}'},
                    "finish_reason": "stop",
                }],
            })
        if failure_kind == "timeout":
            raise httpx2.ReadTimeout(private_details, request=request)
        return httpx2.Response(503, request=request, json={
            "error": {"message": private_details, "type": "server_error"},
        })

    monkeypatch.setattr(httpx2.Client, "send", send)
    monkeypatch.setattr(qa, "retrieve", lambda *args, **kwargs: CHUNKS)
    model = create_chat_model(LlmSettings(
        _env_file=None,
        provider=provider,
        model="test-model",
        base_url="https://private-model.example.test/v1",
        api_key="private-api-key",
    ))
    pipeline = _pipeline(settings, model)
    monkeypatch.setattr(main.app.state, "settings", settings, raising=False)
    monkeypatch.setattr(main.app.state, "index", pipeline.index, raising=False)
    monkeypatch.setattr(main, "QaPipeline", lambda **kwargs: pipeline)
    client = TestClient(main.app, raise_server_exceptions=False)
    try:
        response = client.post("/query", json={"question": "private-question"})
    finally:
        client.close()

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["error"] == "LLM_UNAVAILABLE"
    assert detail["cause"] == "QA_PIPELINE"
    assert set(detail) == {"error", "cause", "detail"}
    expected_error = "Timeout" if failure_kind == "timeout" else "Error"
    assert expected_error in detail["detail"]
    assert all(value not in response.text for value in (
        "private-question", "private-api-key", "private-model.example.test", "Authorization",
    ))
    assert len(requests) == (1 if failing_stage == "judge" else 2)
