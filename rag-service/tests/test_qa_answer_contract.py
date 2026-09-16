"""Completed answers need real evidence and citations visible in the rendered prose."""

import json
from pathlib import Path

import pytest

from app.modules.qa.public import Evidence, Qa, QaError


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
