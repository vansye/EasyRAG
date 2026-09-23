"""历史难例导出的离线契约：替身 Knowledge、只读、不含答案正文、输出限制在 data 目录。"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from app.modules.knowledge.public import DocumentNotFound
from scripts.export_history_failures import collect_failures, main, render_sheet


class _FakeKnowledge:
    def __init__(self, records, titles):
        self.records = {record.id: record for record in records}
        self.titles = titles
        self.calls = []

    def list_history(self, page=0, size=20):
        self.calls.append(("list", page, size))
        ordered = sorted(self.records.values(), key=lambda record: -record.id)
        items = [SimpleNamespace(id=r.id, status=r.status) for r in ordered[page * size:(page + 1) * size]]
        return SimpleNamespace(total=len(ordered), items=items)

    def get_history(self, history_id):
        self.calls.append(("get", history_id))
        return self.records[history_id]

    def get(self, document_id):
        self.calls.append(("document", document_id))
        if document_id not in self.titles:
            raise DocumentNotFound()
        return SimpleNamespace(title=self.titles[document_id])

    def close(self):
        self.calls.append(("close",))


def _record(history_id, status, question, decision, retrieved, relevant=()):
    trace = [{"round_index": 1, "query": question, "decision": decision, "relevant": list(relevant),
              "retrieved": [{"chunk_id": 100 + rank, "document_id": doc, "score": score, "rank": rank}
                            for rank, (doc, score) in enumerate(retrieved, start=1)]}]
    return SimpleNamespace(id=history_id, status=status, question=question, answer="秘密答案正文",
                           model={"provider": "openai", "model": "m"}, created_at=datetime(2026, 9, 22, 10, 0),
                           trace=trace, sources=())


@pytest.fixture
def knowledge():
    records = [
        _record(1, "ANSWERED", "正常回答", "SUFFICIENT", [(1, 0.9)]),
        _record(2, "REFUSED", "库里没有的问题？", "NONE", [(1, 0.61), (2, 0.55)]),
        _record(3, "PARTIAL", "只答了一半 | 带竖线", "PARTIAL", [(2, 0.7), (9, 0.5)], relevant=[1]),
    ]
    return _FakeKnowledge(records, {1: "资料一", 2: "资料二"})


def test_collect_keeps_only_failures_resolves_titles_and_never_reads_answers(knowledge):
    entries = collect_failures(knowledge)
    assert [entry["history_id"] for entry in entries] == [3, 2]
    assert entries[1]["candidates"] == [
        {"rank": 1, "document_id": 1, "chunk_id": 101, "score": 0.61, "title": "资料一"},
        {"rank": 2, "document_id": 2, "chunk_id": 102, "score": 0.55, "title": "资料二"},
    ]
    assert entries[0]["candidates"][1]["title"] is None and entries[0]["relevant"] == [1]
    assert all("answer" not in entry for entry in entries)
    assert ("get", 1) not in knowledge.calls
    assert knowledge.calls.count(("document", 2)) == 1


def test_sheet_escapes_pipes_and_leaves_the_expected_source_column_blank(knowledge):
    sheet = render_sheet(collect_failures(knowledge), "2026-09-22T10:00:00+08:00")
    assert "| 3 | 2026-09-22 10:00 | PARTIAL | 只答了一半 \\| 带竖线 | 1·资料二·0.700；2·（资料已删除）·0.500 | PARTIAL / [1] |  |" in sheet
    assert "秘密答案正文" not in sheet and "共 2 条" in sheet


def test_cli_writes_json_and_sheet_only_under_the_data_directory(tmp_path, monkeypatch, knowledge):
    from scripts import export_history_failures

    monkeypatch.setattr(export_history_failures, "Knowledge", lambda: knowledge)
    monkeypatch.setattr(export_history_failures, "SERVICE_DIR", tmp_path)
    with pytest.raises(SystemExit) as refused:
        main(["--output", str(tmp_path / "outside.json")])
    assert refused.value.code == 2 and ("close",) not in knowledge.calls

    output = tmp_path / "data" / "failures.json"
    assert main(["--output", str(output)]) == 0
    assert output.is_file() and output.with_suffix(".md").is_file()
    assert "秘密答案正文" not in output.read_text(encoding="utf-8")
    assert knowledge.calls[-1] == ("close",)
