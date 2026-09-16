"""Completed answers need real evidence and citations visible in the rendered prose."""

import json
from pathlib import Path

import pytest

from app.modules.qa.public import Evidence, Qa, QaError
from app.modules.qa import public as qa_module


CASES = json.loads((Path(__file__).parent / "contracts/answer-citations.json").read_text(encoding="utf-8"))
EVIDENCE = tuple(Evidence(100 + rank, rank, f"Evidence {rank}", "", 0.9) for rank in range(1, 4))


class Search:
    def __init__(self, evidence=EVIDENCE):
        self.evidence = evidence

    def search(self, query, top_k=5):
        return self.evidence


class Chat:
    def __init__(self, answer, verdict="SUFFICIENT"):
        self.replies = iter([json.dumps({"verdict": verdict}), answer])
        self.calls = 0

    def complete(self, prompt):
        self.calls += 1
        return next(self.replies)


def test_empty_evidence_refuses_without_trusting_or_calling_the_model():
    chat = Chat("Invented evidence [1].")
    result = Qa().answer("An unsupported question", Search(()), chat)
    assert result.status == "REFUSED"
    assert result.chunk_ids == ()
    assert result.trace[0].retrieved == ()
    assert result.trace[0].decision == "NONE"
    assert chat.calls == 0


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
@pytest.mark.parametrize("verdict", ["SUFFICIENT", "PARTIAL"])
def test_prose_citation_contract(case, verdict):
    chat = Chat(case["answer"], verdict)
    if case["valid"]:
        result = Qa().answer("Question", Search(), chat)
        assert result.answer == case["answer"]
        assert result.status == ("ANSWERED" if verdict == "SUFFICIENT" else "PARTIAL")
        assert result.chunk_ids == (101, 102, 103)
    else:
        with pytest.raises(QaError) as failure:
            Qa().answer("Question", Search(), chat)
        assert failure.value.stage == "generate"
        assert failure.value.cause == "INVALID_CITATIONS"
        assert case["answer"] not in str(failure.value)
    assert chat.calls == 2


def test_model_timing_separates_judgement_from_generation(monkeypatch):
    now = [10.0]
    monkeypatch.setattr(qa_module, "perf_counter", lambda: now[0], raising=False)
    chat = Chat("Answer [1]")
    complete = chat.complete

    def timed_complete(prompt):
        now[0] += 0.25 if chat.calls == 0 else 1.5
        return complete(prompt)

    chat.complete = timed_complete
    timings = {}
    result = Qa().answer("Question", Search(), chat, record_timing=timings.__setitem__)
    assert result.status == "ANSWERED" and chat.calls == 2
    assert timings == {"judge": 250.0, "generate": 1500.0}


def test_failed_model_call_still_records_elapsed_time(monkeypatch):
    now = [10.0]
    monkeypatch.setattr(qa_module, "perf_counter", lambda: now[0], raising=False)
    chat = Chat("Unused")

    def fail(prompt):
        now[0] += 0.25
        raise RuntimeError("private provider details")

    chat.complete = fail
    timings = {}
    with pytest.raises(QaError) as failure:
        Qa().answer("Question", Search(), chat, record_timing=timings.__setitem__)
    assert failure.value.stage == "judge"
    assert timings == {"judge": 250.0}
