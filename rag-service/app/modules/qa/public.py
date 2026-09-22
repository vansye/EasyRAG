"""Public boundary for evidence-based answers using consumer-owned capability ports."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Generator
from contextlib import closing
from dataclasses import dataclass
from time import perf_counter
from typing import Literal, Protocol, cast

from ._citations import citations_are_valid


JudgeVerdict = Literal["SUFFICIENT", "PARTIAL", "NONE"]
AnswerStatus = Literal["ANSWERED", "PARTIAL", "REFUSED"]

_REFUSAL_ANSWER = "知识库中没有找到能回答这个问题的内容。"
_MAX_QUESTION_CODE_POINTS = 2000
_VERDICT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)

__all__ = [
    "AnswerDraft", "AnswerStatus", "ChatPort", "Evidence", "JudgeVerdict", "Qa", "QaError",
    "QaTraceEntry", "SearchPort", "TraceHit", "validate_question", "StreamChatPort",
    "StreamCancelled", "QaStreamEvent",
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


class StreamChatPort(ChatPort, Protocol):
    def stream(self, prompt: str) -> Generator[str, None, None]: ...


class StreamCancelled(Exception):
    """The consumer has stopped; no further stages may begin."""


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
    # Ranks the judge named as supporting its decision; generation and citations see only these.
    relevant: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class AnswerDraft:
    answer: str
    status: AnswerStatus
    chunk_ids: tuple[int, ...]
    trace: tuple[QaTraceEntry, ...]


@dataclass(frozen=True, slots=True)
class QaStreamEvent:
    kind: Literal['sources', 'delta', 'done']
    text: str = ''
    chunk_ids: tuple[int, ...] = ()
    trace: tuple[QaTraceEntry, ...] = ()
    draft: AnswerDraft | None = None


def _check_cancelled(cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise StreamCancelled()


def validate_question(question: str) -> None:
    """Validate before a caller acquires resources; retain the original question."""
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must not be blank")
    if len(question) > _MAX_QUESTION_CODE_POINTS:
        raise ValueError("question must contain at most 2000 code points")


def _complete(
    chat: ChatPort, prompt: str, stage: Literal["judge", "generate"],
    record_timing: Callable[[str, float], None] | None = None,
) -> str:
    started = perf_counter()
    try:
        content = chat.complete(prompt)
    except Exception as exc:
        raise QaError(stage, type(exc).__name__) from None
    finally:
        if record_timing is not None:
            record_timing(stage, (perf_counter() - started) * 1000)
    if not isinstance(content, str) or not content.strip():
        raise QaError(stage, "EMPTY_OR_NON_TEXT_CONTENT")
    return content


def _parse_judgement(response: str, evidence_count: int) -> tuple[JudgeVerdict, tuple[int, ...] | None]:
    """Return the verdict and, when the judge named them, the ranks that support it.

    A missing `relevant` keeps every candidate (the pre-C-6 contract); a present but malformed one
    is a wrong instruction from the model and fails like a bad verdict.
    """
    match = _VERDICT_PATTERN.search(response)
    if match is None:
        raise QaError("judge", "INVALID_JUDGE_OUTPUT")
    try:
        payload = json.loads(match.group(0))
        verdict = payload["verdict"]
    except (ValueError, KeyError, TypeError):
        raise QaError("judge", "INVALID_JUDGE_OUTPUT") from None
    if verdict not in ("SUFFICIENT", "PARTIAL", "NONE"):
        raise QaError("judge", "INVALID_JUDGE_OUTPUT")
    if verdict == "NONE" or "relevant" not in payload:
        return cast(JudgeVerdict, verdict), None
    ranks = payload["relevant"]
    if not isinstance(ranks, list) or not ranks or any(
        type(rank) is not int or not 1 <= rank <= evidence_count for rank in ranks
    ):
        raise QaError("judge", "INVALID_JUDGE_OUTPUT")
    return cast(JudgeVerdict, verdict), tuple(dict.fromkeys(ranks))


def _numbered(evidence: tuple[tuple[int, Evidence], ...]) -> str:
    return "\n\n".join(f"[{rank}] {chunk.text}" for rank, chunk in evidence)


def _judge_prompt(question: str, evidence: tuple[tuple[int, Evidence], ...]) -> str:
    numbered = _numbered(evidence)
    return (
        "你是知识库问答的判定器。根据下面的检索片段判断：仅凭这些内容，"
        "能否完整回答用户的问题？\n\n"
        f"问题：{question}\n\n检索片段：\n{numbered}\n\n"
        "判定规则：\n"
        "- SUFFICIENT：片段中有与问题主题直接相关的内容，且足以支撑完整回答\n"
        "- PARTIAL：有直接相关内容，但只覆盖问题的一部分，缺失的是问题主体而非边角\n"
        "- NONE：没有与问题主题直接相关的片段（词面相似不算直接相关）\n\n"
        "同时列出与问题主题直接相关、支撑该判定的片段编号 relevant：回答时只能看到并引用这些片段，"
        "漏掉的片段无法再被引用；SUFFICIENT / PARTIAL 至少列一个，NONE 为空数组。\n"
        '只输出 JSON：{"verdict": "SUFFICIENT", "relevant": [1, 3]}（verdict 三选一），不要输出其他内容。'
    )


def _generate_prompt(question: str, evidence: tuple[tuple[int, Evidence], ...], *, partial: bool) -> str:
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
        "2. 每个事实性结论后用 [n] 标注来源片段编号，编号只能取片段前标注的编号\n\n"
        f"问题：{question}\n\n片段：\n{numbered}{boundary}"
    )


class Qa:
    """Keep each question's evidence, model calls, and trace local to that call."""

    __slots__ = ()

    def _prepare(
        self, question: str, search: SearchPort, chat: ChatPort, top_k: int = 5,
        *, record_timing: Callable[[str, float], None] | None = None,
        cancelled: Callable[[], bool] = lambda: False,
    ):
        """Retrieve, judge, and return the numbered evidence the answer may see (a subset when the judge named it)."""
        validate_question(question)
        if type(top_k) is not int or top_k <= 0:
            raise ValueError("top_k must be positive")
        _check_cancelled(cancelled)
        try:
            evidence = search.search(question, top_k=top_k)
        except Exception as exc:
            raise QaError("search", type(exc).__name__) from None
        _check_cancelled(cancelled)
        numbered = tuple(enumerate(evidence, start=1))
        verdict, relevant = _parse_judgement(_complete(
            chat, _judge_prompt(question, numbered), "judge", record_timing,
        ), len(evidence)) if evidence else ("NONE", None)
        _check_cancelled(cancelled)
        selected = tuple(pair for pair in numbered if pair[0] in relevant) if relevant else numbered
        trace = (QaTraceEntry(
            round_index=1,
            query=question,
            retrieved=tuple(
                TraceHit(chunk.chunk_id, chunk.document_id, chunk.score, rank)
                for rank, chunk in numbered
            ),
            decision=verdict,
            relevant=tuple(rank for rank, _chunk in selected) if verdict != "NONE" else (),
        ),)
        return selected, verdict, trace

    def answer(
        self, question: str, search: SearchPort, chat: ChatPort, top_k: int = 5,
        *, record_timing: Callable[[str, float], None] | None = None,
    ) -> AnswerDraft:
        selected, verdict, trace = self._prepare(
            question, search, chat, top_k, record_timing=record_timing,
        )
        if verdict == "NONE":
            return AnswerDraft(
                answer=_REFUSAL_ANSWER, status="REFUSED", chunk_ids=(), trace=trace,
            )
        answer = _complete(
            chat, _generate_prompt(question, selected, partial=(verdict == "PARTIAL")), "generate",
            record_timing,
        )
        if not citations_are_valid(answer, {rank for rank, _chunk in selected}):
            raise QaError("generate", "INVALID_CITATIONS")
        return AnswerDraft(
            answer=answer,
            status="ANSWERED" if verdict == "SUFFICIENT" else "PARTIAL",
            chunk_ids=tuple(sorted({chunk.chunk_id for _rank, chunk in selected})),
            trace=trace,
        )

    def stream(
        self, question: str, search: SearchPort, chat: StreamChatPort, top_k: int = 5,
        *, record_timing: Callable[[str, float], None] | None = None,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> Generator[QaStreamEvent, None, None]:
        selected, verdict, trace = self._prepare(
            question, search, chat, top_k, record_timing=record_timing, cancelled=cancelled,
        )
        if verdict == 'NONE':
            yield QaStreamEvent('done', draft=AnswerDraft(_REFUSAL_ANSWER, 'REFUSED', (), trace))
            return
        chunk_ids = tuple(sorted({chunk.chunk_id for _rank, chunk in selected}))
        yield QaStreamEvent('sources', chunk_ids=chunk_ids, trace=trace)
        _check_cancelled(cancelled)
        started = perf_counter()
        parts = []
        try:
            with closing(chat.stream(_generate_prompt(question, selected, partial=verdict == 'PARTIAL'))) as chunks:
                while True:
                    _check_cancelled(cancelled)
                    try:
                        text = next(chunks)
                    except StopIteration:
                        break
                    _check_cancelled(cancelled)
                    if not isinstance(text, str):
                        raise QaError('generate', 'EMPTY_OR_NON_TEXT_CONTENT')
                    if text:
                        parts.append(text)
                        yield QaStreamEvent('delta', text=text)
        except (StreamCancelled, QaError):
            raise
        except Exception as failure:
            raise QaError('generate', type(failure).__name__) from None
        finally:
            if record_timing is not None:
                record_timing('generate', (perf_counter() - started) * 1000)
        _check_cancelled(cancelled)
        answer = ''.join(parts)
        if not answer.strip():
            raise QaError('generate', 'EMPTY_OR_NON_TEXT_CONTENT')
        if not citations_are_valid(answer, {rank for rank, _chunk in selected}):
            raise QaError('generate', 'INVALID_CITATIONS')
        yield QaStreamEvent('done', draft=AnswerDraft(
            answer, 'ANSWERED' if verdict == 'SUFFICIENT' else 'PARTIAL', chunk_ids, trace,
        ))
