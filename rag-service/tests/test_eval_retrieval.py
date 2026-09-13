"""离线验收用真实 Chroma 测检索，HTTP 模拟仅隔离外部模型服务。"""

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import chromadb
from chromadb.config import Settings as ChromaSettings
import httpx
import pytest
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace


def test_golden_parser_excludes_calibration_tables():
    from scripts.eval_retrieval import parse_questions

    golden_path = Path(__file__).resolve().parents[2] / "docs/eval/golden-set-v1.md"
    questions = parse_questions(golden_path.read_text(encoding="utf-8"))

    assert len(questions) == 30
    assert {category: sum(question.category == category for question in questions)
            for category in "QNPR"} == {"Q": 16, "N": 7, "P": 1, "R": 6}
    assert len({question.question_id for question in questions}) == 30
    assert questions[0].expected_source == "数据库/知识条目/ACID.md"
    assert all(question.expected_source is None for question in questions if question.category in "NP")


def test_duplicate_questions_fail_instead_of_changing_the_denominator():
    from scripts.eval_retrieval import parse_questions

    with pytest.raises(ValueError, match="duplicate"):
        parse_questions("| Q1 | first | a.md |\n| Q1 | duplicate | a.md |")


def test_hit_at_k_curve_counts_chunks_not_distinct_documents():
    from scripts.eval_retrieval import K_VALUES, Question, first_hit_rank, summarize

    questions = [
        Question("Q1", "first", "docs/a.md"),
        Question("Q2", "second", "docs/b.md"),
        Question("R1", "original wording", "docs/c.md"),
        Question("N1", "out of scope", None),
        Question("P1", "partially covered", None),
    ]
    rankings = {
        "Q1": [{"source": "other.md"}] * 4 + [{"source": "docs/a.md"}],
        "Q2": [{"source": "b.md"}] * 5 + [{"source": "docs/b.md"}],
        "R1": [{"source": "docs/c.md"}],
        "N1": [{"source": "docs/a.md"}],
        "P1": [{"source": "docs/c.md"}],
    }

    assert first_hit_rank(questions[0], rankings["Q1"]) == 5
    # 检索深度从 5 提到 10 后，第 6 名不再是"未命中"——它计入 hit@10
    assert first_hit_rank(questions[1], rankings["Q2"]) == 6
    metrics = summarize(questions, rankings)
    assert metrics["Q"]["total"] == 2
    assert metrics["Q"]["hits_at_k"] == {1: 0, 3: 0, 5: 1, 10: 2}
    assert metrics["Q"]["hit_rate_at_k"][5] == 0.5
    assert metrics["R"]["hits_at_k"] == {k: 1 for k in K_VALUES}
    assert metrics["overall"]["hits_at_k"] == {1: 1, 3: 1, 5: 2, 10: 3}
    assert metrics["excluded"] == {"N": 1, "P": 1}


def test_tokenizer_disables_saved_truncation_and_padding(tmp_path):
    from scripts.eval_retrieval import load_tokenizer

    tokenizer = Tokenizer(WordLevel({"[UNK]": 0, "one": 1, "two": 2, "three": 3}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = Whitespace()
    tokenizer.enable_truncation(max_length=1)
    tokenizer.enable_padding(length=4)
    path = tmp_path / "tokenizer.json"
    tokenizer.save(str(path))

    loaded = load_tokenizer(path)

    assert loaded.encode("one two three").ids == [1, 2, 3]
    assert loaded.truncation is None
    assert loaded.padding is None


def test_prepare_corpus_preserves_original_bytes_and_fingerprints(tmp_path):
    from scripts.eval_retrieval import prepare_corpus

    original = "# 示例🙂\r\n正文。\r\n".encode("utf-8")
    (tmp_path / "example.md").write_bytes(original)

    records, manifest = prepare_corpus(tmp_path, count_tokens=len, max_tokens=512, min_tokens=0)

    assert len(records) == 1
    assert records[0].source == "example.md"
    assert records[0].chunk.text.encode("utf-8") == original
    assert records[0].chunk.byte_end == len(original)
    assert manifest == [{
        "source": "example.md", "sha256": hashlib.sha256(original).hexdigest(),
        "bytes": len(original), "chunks": 1,
    }]


def test_embedding_batches_keep_order_and_disable_truncation():
    from scripts.eval_retrieval import embed_texts

    requests = []
    expected = {"alpha": [1.0, 0.0], "beta": [0.0, 1.0], "gamma": [-1.0, 0.0]}

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert request.url.path == "/api/embed"
        return httpx.Response(200, json={"embeddings": [expected[text] for text in payload["input"]]})

    with httpx.Client(base_url="http://ollama", transport=httpx.MockTransport(respond)) as client:
        vectors = embed_texts(client, list(expected), model="bge-m3", dimensions=2, batch_size=2)

    assert vectors == list(expected.values())
    assert [request["input"] for request in requests] == [["alpha", "beta"], ["gamma"]]
    assert all(request["truncate"] is False and request["model"] == "bge-m3" for request in requests)


@pytest.mark.parametrize("vectors,message", [([], "count"), ([[1.0, 2.0, 3.0]], "dimension")])
def test_embedding_rejects_wrong_count_or_dimension(vectors, message):
    from scripts.eval_retrieval import embed_texts

    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"embeddings": vectors}))
    with httpx.Client(base_url="http://ollama", transport=transport) as client:
        with pytest.raises(ValueError, match=message):
            embed_texts(client, ["text"], model="bge-m3", dimensions=2, batch_size=1)


def test_embedding_http_failure_is_not_silently_retried():
    from scripts.eval_retrieval import embed_texts

    requests = []

    def unavailable(request):
        requests.append(request)
        return httpx.Response(503, json={"error": "unavailable"})

    with httpx.Client(base_url="http://ollama", transport=httpx.MockTransport(unavailable)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            embed_texts(client, ["text"], model="bge-m3", dimensions=2, batch_size=1)
    assert len(requests) == 1


def test_cosine_retrieval_cleans_up_only_its_ephemeral_collection(monkeypatch):
    from app.chunking import Chunk
    from scripts.eval_retrieval import IndexedChunk, Question, retrieve_chunks

    client = chromadb.EphemeralClient(settings=ChromaSettings(anonymized_telemetry=False))
    sentinel_name = f"sentinel-{uuid4().hex}"
    sentinel = client.create_collection(sentinel_name, embedding_function=None)
    sentinel.add(ids=["keep"], embeddings=[[1.0, 0.0]], documents=["untouched"])
    before = {collection.name for collection in client.list_collections()}

    def forbid_persistent_client(*args, **kwargs):
        pytest.fail("offline evaluation must not open a persistent index")

    monkeypatch.setattr(chromadb, "PersistentClient", forbid_persistent_client)
    records = [IndexedChunk(source, 1, Chunk(source, "heading", 0, len(source), len(source)))
               for source in ["a.md", "b.md", "c.md"]]
    try:
        rankings = retrieve_chunks(
            records, [[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]],
            [Question("Q1", "find a", "a.md")], [[1.0, 0.0]],
        )

        assert [hit["source"] for hit in rankings["Q1"]] == ["a.md", "b.md", "c.md"]
        assert rankings["Q1"][0]["distance"] == pytest.approx(0.0)
        assert rankings["Q1"][0]["byte_end"] == 4
        assert {collection.name for collection in client.list_collections()} == before
        assert sentinel.get(ids=["keep"])["ids"] == ["keep"]
    finally:
        client.delete_collection(sentinel_name)


@pytest.fixture
def evaluation_files(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "alpha.md").write_text("# Alpha\nalpha", encoding="utf-8")
    (corpus / "beta.md").write_text("# Beta\nbeta", encoding="utf-8")
    golden = tmp_path / "golden.md"
    golden.write_text(
        "| Q1 | What is alpha? | alpha.md |\n"
        "| R1 | Explain beta. | beta.md |\n"
        "| N1 | unknown | absent |\n"
        "| P1 | partial | incomplete |\n", encoding="utf-8",
    )
    tokenizer = Tokenizer(WordLevel({"[UNK]": 0, "alpha": 1, "beta": 2}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = Whitespace()
    tokenizer_path = tmp_path / "tokenizer.json"
    tokenizer.save(str(tokenizer_path))
    return corpus, golden, tokenizer_path


def test_cli_writes_report_using_real_index_and_explicit_unscored_categories(
    evaluation_files, tmp_path, monkeypatch, capsys,
):
    from scripts import eval_retrieval

    corpus, golden, tokenizer = evaluation_files
    report = tmp_path / "baseline.md"
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "bge-m3:latest", "digest": "model-fixture-digest"}]})
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "fixture-version"})
        assert request.url.path == "/api/embed"
        payload = json.loads(request.content)
        vectors = [[1.0, 0.0] if "alpha" in text.lower() else [0.0, 1.0] for text in payload["input"]]
        return httpx.Response(200, json={"embeddings": vectors})

    client = httpx.Client(base_url="http://ollama", transport=httpx.MockTransport(respond))
    monkeypatch.setattr(eval_retrieval.httpx, "Client", lambda **kwargs: client)
    status = eval_retrieval.main([
        "--corpus", str(corpus), "--golden-set", str(golden), "--tokenizer", str(tokenizer),
        "--ollama-url", "http://ollama", "--dimensions", "2", "--min-tokens", "0",
        "--output", str(report),
    ])

    assert status == 0
    contents = report.read_text(encoding="utf-8")
    curve_row = " | ".join("1/1（100.00%）" for _ in range(4))
    assert f"| Q | {curve_row} |" in contents
    assert f"| R | {curve_row} |" in contents
    assert f"| Q+R | " + " | ".join("2/2（100.00%）" for _ in range(4)) + " |" in contents
    assert "漏召回题（前 10 均未命中）：无。" in contents
    assert "### N1 · 未评分" in contents
    assert "### P1 · 未评分" in contents
    assert "model-fixture-digest" in contents
    assert hashlib.sha256(golden.read_bytes()).hexdigest() in contents
    assert hashlib.sha256(tokenizer.read_bytes()).hexdigest() in contents
    assert "UTF-8" in contents and "byte_start" in contents
    assert "scripts.eval_retrieval" in contents
    output = capsys.readouterr().out
    assert "Q: hit@1=1/1" in output
    assert "hit@5=2/2" in output
    assert sum(request.url.path == "/api/embed" for request in requests) == 2


@pytest.mark.parametrize("invalid_golden", ["# no questions", "| Q1 | missing | nonexistent.md |"])
def test_cli_rejects_invalid_evaluation_inputs_before_network(
    invalid_golden, evaluation_files, monkeypatch,
):
    from scripts import eval_retrieval

    corpus, golden, tokenizer = evaluation_files
    golden.write_text(invalid_golden, encoding="utf-8")

    def forbidden_network(**kwargs):
        pytest.fail("invalid golden data must fail before any network call")

    monkeypatch.setattr(eval_retrieval.httpx, "Client", forbidden_network)
    with pytest.raises(SystemExit) as failure:
        eval_retrieval.main([
            "--corpus", str(corpus), "--golden-set", str(golden), "--tokenizer", str(tokenizer),
        ])
    assert failure.value.code == 2
