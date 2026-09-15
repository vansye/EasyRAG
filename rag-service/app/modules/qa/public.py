"""Public boundary for evidence-based answers using consumer-owned capability ports."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal, Protocol, cast


JudgeVerdict = Literal["SUFFICIENT", "PARTIAL", "NONE"]
AnswerStatus = Literal["ANSWERED", "PARTIAL", "REFUSED"]

_REFUSAL_ANSWER = "知识库中没有找到能回答这个问题的内容。"
_MAX_QUESTION_CODE_POINTS = 2000
_VERDICT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)

__all__ = [
    "AnswerDraft", "AnswerStatus", "ChatPort", "Evidence", "JudgeVerdict", "Qa", "QaError",
    "QaTraceEntry", "SearchPort", "TraceHit", "validate_question",
]


class QaError(RuntimeError):
    """A technical failure, distinct from refusal, containing no upstream body."""

    def __init__(self, stage: Literal["search", "judge", "generate"], cause: str) -> None:
        self.stage = stage
        self.cause = cause
        super().__init__(f"{stage} failed: {cause}")


@dataclass(frozen=True, slots=True)
class Evidence:
    chunk_id: int
    document_id: int
    text: str
    heading_path: str
    score: float


class SearchPort(Protocol):
    def search(self, query: str, top_k: int = 5) -> tuple[Evidence, ...]: ...


class ChatPort(Protocol):
    def complete(self, prompt: str) -> str: ...


@dataclass(frozen=True, slots=True)
class TraceHit:
    chunk_id: int
    document_id: int
    score: float
    rank: int


@dataclass(frozen=True, slots=True)
class QaTraceEntry:
    round_index: int
    query: str
    retrieved: tuple[TraceHit, ...]
    decision: JudgeVerdict


@dataclass(frozen=True, slots=True)
class AnswerDraft:
    answer: str
    status: AnswerStatus
    chunk_ids: tuple[int, ...]
    trace: tuple[QaTraceEntry, ...]


def validate_question(question: str) -> None:
    """Validate before a caller acquires resources; retain the original question."""
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must not be blank")
    if len(question) > _MAX_QUESTION_CODE_POINTS:
        raise ValueError("question must contain at most 2000 code points")


def _complete(chat: ChatPort, prompt: str, stage: Literal["judge", "generate"]) -> str:
    try:
        content = chat.complete(prompt)
    except Exception as exc:
        raise QaError(stage, type(exc).__name__) from None
    if not isinstance(content, str) or not content.strip():
        raise QaError(stage, "EMPTY_OR_NON_TEXT_CONTENT")
    return content


def _parse_verdict(response: str) -> JudgeVerdict:
    match = _VERDICT_PATTERN.search(response)
    if match is None:
        raise QaError("judge", "INVALID_JUDGE_OUTPUT")
    try:
        verdict = json.loads(match.group(0))["verdict"]
    except (ValueError, KeyError):
        raise QaError("judge", "INVALID_JUDGE_OUTPUT") from None
    if verdict not in ("SUFFICIENT", "PARTIAL", "NONE"):
        raise QaError("judge", "INVALID_JUDGE_OUTPUT")
    return cast(JudgeVerdict, verdict)


def _numbered(evidence: tuple[Evidence, ...]) -> str:
    return "\n\n".join(
        f"[{rank}] {chunk.text}" for rank, chunk in enumerate(evidence, start=1)
    )


def _judge_prompt(question: str, evidence: tuple[Evidence, ...]) -> str:
    numbered = _numbered(evidence)
    return (
        "你是知识库问答的判定器。根据下面的检索片段判断：仅凭这些内容，"
        "能否完整回答用户的问题？\n\n"
        f"问题：{question}\n\n检索片段：\n{numbered}\n\n"
        "判定规则：\n"
        "- SUFFICIENT：片段中有与问题主题直接相关的内容，且足以支撑完整回答\n"
        "- PARTIAL：有直接相关内容，但只覆盖问题的一部分，缺失的是问题主体而非边角\n"
        "- NONE：没有与问题主题直接相关的片段（词面相似不算直接相关）\n\n"
        '只输出 JSON：{"verdict": "SUFFICIENT"}（三选一），不要输出其他内容。'
    )


def _generate_prompt(question: str, evidence: tuple[Evidence, ...], *, partial: bool) -> str:
    numbered = _numbered(evidence)
    boundary = ""
    if partial:
        boundary = (
            "\n注意：以上片段只覆盖了问题的一部分。回答时先基于已有内容作答，"
            "并明确声明覆盖边界（说明库中只有这些相关内容，未覆盖的部分无法回答）。"
        )
    return (
        "根据下面的知识库片段回答问题。要求：\n"
        "1. 只使用片段中出现的信息，不得编造或补充片段之外的知识\n"
        "2. 每个事实性结论后用 [n] 标注来源片段编号\n\n"
        f"问题：{question}\n\n片段：\n{numbered}{boundary}"
    )


class Qa:
    """Keep each question's evidence, model calls, and trace local to that call."""

    __slots__ = ()

    def answer(
        self, question: str, search: SearchPort, chat: ChatPort, top_k: int = 5,
    ) -> AnswerDraft:
        validate_question(question)
        if type(top_k) is not int or top_k <= 0:
            raise ValueError("top_k must be positive")
        try:
            evidence = search.search(question, top_k=top_k)
        except Exception as exc:
            raise QaError("search", type(exc).__name__) from None
        verdict = _parse_verdict(_complete(chat, _judge_prompt(question, evidence), "judge"))
        trace = (QaTraceEntry(
            round_index=1,
            query=question,
            retrieved=tuple(
                TraceHit(chunk.chunk_id, chunk.document_id, chunk.score, rank)
                for rank, chunk in enumerate(evidence, start=1)
            ),
            decision=verdict,
        ),)
        if verdict == "NONE":
            return AnswerDraft(
                answer=_REFUSAL_ANSWER, status="REFUSED", chunk_ids=(), trace=trace,
            )
        answer = _complete(
            chat, _generate_prompt(question, evidence, partial=(verdict == "PARTIAL")), "generate",
        )
        return AnswerDraft(
            answer=answer,
            status="ANSWERED" if verdict == "SUFFICIENT" else "PARTIAL",
            chunk_ids=tuple(sorted({chunk.chunk_id for chunk in evidence})),
            trace=trace,
        )
