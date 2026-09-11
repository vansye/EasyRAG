"""/health 的行为测试。

核心断言与 Java 侧对称：**依赖不可用时，进程仍报 UP**。
Java 的索引状态机靠 /health 区分"Python 挂了"与"模型服务挂了"，
如果 embedding 一断就整包 503，FAILED 的归因就会错。

本测试不需要 Ollama 在线——embedding 探针被打桩，两种情况都覆盖。
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app


@pytest.fixture
def clean_env(monkeypatch):
    """清掉所有本服务的环境变量。

    `Settings(_env_file=None)` 只关掉 .env 文件，`os.environ` 照读——
    在同一个 shell 里既起服务又跑测试是常态（.env.example 教用户设的
    就是 EMBEDDING_MODEL），不清就会让测试结果取决于 shell 状态。
    """
    for key in list(os.environ):
        if key.startswith(("EMBEDDING_", "CHUNK_", "EMBED_")):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def client(tmp_path, monkeypatch, clean_env):
    """每个用例用独立的 Chroma 目录，避免测试间互相污染。"""
    from app.config import Settings, get_settings

    get_settings.cache_clear()
    settings = Settings(_env_file=None)
    monkeypatch.setattr(type(settings), "chroma_dir", property(lambda _self: tmp_path / "chroma"))
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    monkeypatch.setattr("app.main.get_settings", lambda: settings)

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()


@pytest.fixture
def httpx_stub(monkeypatch):
    """打桩 embedding 端点的 HTTP 响应，让测试不依赖真实 Ollama 在线。

    返回一个函数：传入 /api/tags 的 JSON 体，即模拟该端点的成功响应。
    """

    def _install(payload: dict):
        class _StubResponse:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return payload

        class _StubClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc_info):
                return False

            async def get(self, _url):
                return _StubResponse()

        monkeypatch.setattr("app.main.httpx.AsyncClient", _StubClient)

    return _install


def test_health_reports_up_when_embedding_reachable(client, monkeypatch):
    """embedding 可达时：整体 UP，各分项如实报告。"""

    async def fake_probe(_settings):
        return {"provider": "ollama", "model": "bge-m3", "dim": 1024,
                "reachable": True, "status": "UP"}

    monkeypatch.setattr(main, "_probe_embedding", fake_probe)

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "UP"
    assert body["service"] == "easyrag-rag-service"
    assert body["embedding"]["status"] == "UP"
    assert body["chroma"]["status"] == "UP"


def test_health_stays_up_when_embedding_unreachable(client, monkeypatch):
    """embedding 不可达时：仍返回 200 且进程 UP，只有 embedding 转 DOWN。

    这是 Java 侧 FAILED 归因的前提，不能退化成整包 503。
    """

    async def fake_probe(_settings):
        return {"provider": "ollama", "model": "bge-m3", "dim": 1024,
                "reachable": False, "status": "DOWN", "error": "ConnectError"}

    monkeypatch.setattr(main, "_probe_embedding", fake_probe)

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "UP"          # 关键：进程活着是独立事实
    assert body["embedding"]["status"] == "DOWN"


def test_bm25_starts_empty(client, monkeypatch):
    """BM25 空启动（B-7）：进程起来时 0 条，不向任何人拉数据。

    这条守的是推模式边界——若哪天有人给 BM25 加了"启动时从 Java 拉全量"，
    本测试会失败。
    """

    async def fake_probe(_settings):
        return {"status": "UP", "reachable": True, "model": "bge-m3", "dim": 1024,
                "provider": "ollama"}

    monkeypatch.setattr(main, "_probe_embedding", fake_probe)

    body = client.get("/health").json()

    assert body["bm25"]["chunks"] == 0


def test_collection_name_carries_model_name(clean_env):
    """collection 名带 embedding 模型名：换模型即换 collection。

    这让"换模型"成为显式动作——旧向量与新向量不可比，
    混在一个 collection 里检索会静默劣化。名字里还带维度，见
    TestCollectionNaming.test_collection_name_carries_dim。
    """
    from app.config import Settings

    settings = Settings(_env_file=None, embedding_model="nomic-embed-text", embedding_dim=768)

    assert settings.collection_name == "easyrag_nomic-embed-text_768"


def test_rejects_non_positive_embedding_dim():
    """维度守门：非正维度在配置层就被拒绝，不留到检索时才爆。"""
    from app.config import Settings

    with pytest.raises(ValueError):
        Settings(_env_file=None, embedding_dim=0)


class TestModelPresence:
    """探针必须校验"配置的模型确实存在"，而不只是"端点能响应"。

    背景：Ollama 在线但没拉 bge-m3 时，/api/tags 照常 200。若探针只看
    可达性，健康检查会绿着，直到 M2 第一次 embed 才炸——绿的健康检查
    配上坏掉的现实，是最难查的一类故障。
    """

    def test_matches_model_with_latest_tag(self):
        """Ollama 列表里是 `bge-m3:latest`，配置里写 `bge-m3`，应算命中。"""
        assert main._model_present("bge-m3", {"bge-m3:latest", "qwen2.5:7b"})

    def test_matches_exact_name(self):
        assert main._model_present("bge-m3:latest", {"bge-m3:latest"})

    def test_detects_missing_model(self):
        """本机只有 nomic-embed-text 时，配置 bge-m3 必须判为缺失。"""
        assert not main._model_present("bge-m3", {"nomic-embed-text:latest", "qwen2.5:7b"})

    def test_extracts_ollama_model_names(self):
        payload = {"models": [{"name": "bge-m3:latest"}, {"name": "qwen2.5:7b"}]}

        assert main._extract_model_names("ollama", payload) == {"bge-m3:latest", "qwen2.5:7b"}

    def test_extracts_openai_model_names(self):
        payload = {"data": [{"id": "text-embedding-3-small"}]}

        assert main._extract_model_names("openai", payload) == {"text-embedding-3-small"}


def test_health_reports_down_when_configured_model_missing(client, httpx_stub):
    """端点可达但配置的模型不在 → embedding 判 DOWN 并列出实际可用模型。

    宁可现在红，也不要 M2 时才炸。
    """
    httpx_stub({"models": [{"name": "nomic-embed-text:latest"}]})

    body = client.get("/health").json()

    assert body["status"] == "UP"                      # 进程仍活着
    assert body["embedding"]["reachable"] is True      # 端点确实通
    assert body["embedding"]["model_available"] is False
    assert body["embedding"]["status"] == "DOWN"       # 但模型不在 → 判红
    assert body["embedding"]["error"] == "MODEL_NOT_FOUND"
    assert "nomic-embed-text:latest" in body["embedding"]["available_models"]


def test_health_reports_up_when_configured_model_present(client, httpx_stub):
    """配置的模型确实在列表里 → UP。"""
    httpx_stub({"models": [{"name": "bge-m3:latest"}]})

    body = client.get("/health").json()

    assert body["embedding"]["model_available"] is True
    assert body["embedding"]["status"] == "UP"


class TestMalformedUpstreamResponse:
    """上游返回"200 但结构不对"时，/health 必须仍是 200。

    这是最容易发生的错配：EMBEDDING_BASE_URL 填成了 web 服务器根路径、
    或走了带门户页的反向代理——端点返回 200 和一段 HTML。

    若解析异常逃逸，/health 会 500，Java 就把它归因成"Python 挂了"，
    而分层健康检查存在的全部理由正是防止这种误判。
    """

    @pytest.mark.parametrize(
        "payload,label",
        [
            ([], "合法 JSON 但不是对象"),
            ({"models": None}, "列表字段为 null"),
            ({"models": ["bge-m3"]}, "元素是字符串不是对象"),
            ({}, "缺少列表字段"),
        ],
    )
    def test_stays_200_on_structurally_invalid_payload(self, client, httpx_stub, payload, label):
        httpx_stub(payload)

        response = client.get("/health")

        assert response.status_code == 200, f"{label} 导致 /health 500"
        body = response.json()
        assert body["status"] == "UP"                       # 进程活着是独立事实
        assert body["embedding"]["reachable"] is True       # 端点确实通了
        assert body["embedding"]["status"] == "DOWN"        # 但响应不可用
        assert body["embedding"]["error"] == "BAD_RESPONSE"

    def test_stays_200_when_body_is_not_json(self, client, monkeypatch):
        """上游返回 HTML（网关首页）——json() 直接抛。"""

        class _HtmlResponse:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                raise ValueError("Expecting value: line 1 column 1 (char 0)")

        class _StubClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc_info):
                return False

            async def get(self, _url):
                return _HtmlResponse()

        monkeypatch.setattr("app.main.httpx.AsyncClient", _StubClient)

        response = client.get("/health")

        assert response.status_code == 200
        assert response.json()["embedding"]["error"] == "BAD_RESPONSE"


class TestCollectionNaming:
    """collection 名必须是 Chroma 能接受的，且把维度编进去。"""

    @pytest.mark.parametrize(
        "model",
        [
            "bge-m3:latest",              # Ollama 默认标签写法
            "Qwen/Qwen3-Embedding-0.6B",  # HF 风格，vLLM / Xinference 常见
            "text-embedding-3-small",     # 已经合法的，应保持不变形
        ],
    )
    def test_collection_name_is_chroma_legal(self, clean_env, model):
        """Chroma 只接受 [a-zA-Z0-9._-]。非法字符不净化会让进程起不来，
        而这些都是合法配置值——`_model_present` 自己就为 `:latest` 做了兼容。
        """
        import re

        from app.config import Settings

        name = Settings(_env_file=None, embedding_model=model).collection_name

        assert re.fullmatch(r"[a-zA-Z0-9._-]+", name), f"{model!r} 产生了非法 collection 名 {name!r}"

    def test_collection_name_carries_dim(self, clean_env):
        """维度进名字：同一模型改成截断输出时，旧 collection 不会被静默复用。"""
        from app.config import Settings

        base = Settings(_env_file=None, embedding_model="bge-m3", embedding_dim=1024)
        truncated = Settings(_env_file=None, embedding_model="bge-m3", embedding_dim=512)

        assert base.collection_name != truncated.collection_name

    def test_actually_creatable_in_chroma(self, tmp_path, clean_env):
        """端到端验证：净化后的名字 Chroma 真的能建。"""
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        from app.config import Settings

        name = Settings(_env_file=None, embedding_model="bge-m3:latest").collection_name
        chromadb.PersistentClient(
            path=str(tmp_path / "chroma"),
            settings=ChromaSettings(anonymized_telemetry=False),
        ).get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})


class TestDimensionGuard:
    """维度守门：文档承诺"与索引元信息不一致则拒绝启动"，这里验证它真的生效。"""

    def test_rejects_startup_when_recorded_dim_differs(self, tmp_path, clean_env, monkeypatch):
        """collection 已由 1024 维建立，配置改成 512 → 拒绝启动。

        这条守的是最隐蔽的错配：只改 EMBEDDING_DIM 不改模型名。
        """
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        from app.config import Settings
        from app.index_store import IndexMetadataMismatch, IndexStore

        chroma_dir = tmp_path / "chroma"
        monkeypatch.setattr(
            Settings, "chroma_dir", property(lambda _self: chroma_dir)
        )

        cfg = Settings(_env_file=None, embedding_model="bge-m3", embedding_dim=512)
        # 先手工建一个"同名但元信息记着 1024 维"的 collection
        chroma_dir.mkdir(parents=True, exist_ok=True)
        chromadb.PersistentClient(
            path=str(chroma_dir), settings=ChromaSettings(anonymized_telemetry=False)
        ).get_or_create_collection(
            name=cfg.collection_name,
            metadata={
                "hnsw:space": "cosine",
                "embedding_model": "bge-m3",
                "embedding_dim": 1024,
            },
        )

        with pytest.raises(IndexMetadataMismatch) as excinfo:
            IndexStore(cfg)

        assert "512" in str(excinfo.value) and "1024" in str(excinfo.value)

    def test_records_model_and_dim_in_metadata(self, tmp_path, clean_env, monkeypatch):
        """新建的 collection 必须记下模型与维度，否则守门无比对对象。"""
        from app.config import Settings
        from app.index_store import IndexStore

        chroma_dir = tmp_path / "chroma"
        monkeypatch.setattr(Settings, "chroma_dir", property(lambda _self: chroma_dir))

        store = IndexStore(Settings(_env_file=None, embedding_model="bge-m3", embedding_dim=1024))

        assert store.chroma_error is None
        meta = store._collection.metadata
        assert meta["embedding_model"] == "bge-m3"
        assert int(meta["embedding_dim"]) == 1024


def test_process_starts_even_when_chroma_dir_corrupted(tmp_path, clean_env, monkeypatch):
    """Chroma 数据损坏时，进程仍要起得来，由 /health 如实报 DOWN。

    与 Java 侧对称——"进程活着"是独立于依赖的事实。修复前这里会直接
    在 lifespan 崩掉，`chroma: DOWN` 这个状态在现实中根本不可达。
    """
    from app.config import Settings
    from app.index_store import IndexStore

    chroma_dir = tmp_path / "chroma"
    chroma_dir.mkdir(parents=True)
    (chroma_dir / "chroma.sqlite3").write_bytes(b"this is not a sqlite database")
    monkeypatch.setattr(Settings, "chroma_dir", property(lambda _self: chroma_dir))

    store = IndexStore(Settings(_env_file=None))

    assert store.chroma_error is not None          # 记下了失败原因
    assert store.probe()["status"] == "DOWN"       # 如实报告
    assert store.bm25.size == 0                    # BM25 不受影响
