"""Compose search, one model session, QA, and final citation source resolution."""

import logging
from dataclasses import dataclass

from app.modules.answer_models.public import Models, ModelUnavailable
from app.modules.knowledge.public import DatabaseUnavailable, Knowledge, Source
from app.modules.qa.public import Evidence, Qa, QaError, QaTraceEntry, validate_question
from app.modules.retrieval.public import Retrieval

from .errors import GateBusy, QuestionFailed, RecoveryFailed
from .gate import Gate, Operation


logger = logging.getLogger(__name__)


class _Search:
    def __init__(self, retrieval: Retrieval):
        self._retrieval = retrieval

    def search(self, query: str, top_k: int = 5) -> tuple[Evidence, ...]:
        return tuple(Evidence(hit.chunk_id,hit.document_id,hit.text,hit.heading_path,hit.score)
                     for hit in self._retrieval.search(query,top_k=top_k))


@dataclass(frozen=True)
class AnsweredQuestion:
    answer: str
    status: str
    sources: tuple[Source, ...]
    trace: tuple[QaTraceEntry, ...]


class Questions:
    def __init__(self, knowledge: Knowledge, retrieval: Retrieval, models: Models, gate: Gate, *, qa: Qa | None = None):
        self._knowledge,self._retrieval,self._models,self._gate = knowledge,retrieval,models,gate
        self._qa = qa if qa is not None else Qa()

    def ask(self, question: str) -> AnsweredQuestion:
        validate_question(question)
        admission = self._gate.try_acquire(Operation.QUERY)
        if admission.lease is None:
            raise GateBusy(admission.state)
        with admission.lease:
            try:
                session = self._models.open_session()
                answer = self._qa.answer(question,_Search(self._retrieval),session)
                sources = self._knowledge.sources(answer.chunk_ids) if answer.chunk_ids else ()
            except (ModelUnavailable,QaError,DatabaseUnavailable) as failure:
                logger.warning('question_failed cause=%s stage=%s', type(failure).__name__,
                               getattr(failure,'stage','SESSION_OR_SOURCES'))
                raise QuestionFailed() from None
            by_id = {source.chunk_id:source for source in sources}
            ranks = {hit.chunk_id:hit.rank for hit in answer.trace[-1].retrieved} if answer.trace else {}
            if set(by_id) != set(answer.chunk_ids) or not set(answer.chunk_ids) <= ranks.keys():
                self._gate.require_recovery()
                raise RecoveryFailed('MISSING_CITATION_SOURCE')
            ordered = tuple(by_id[chunk_id] for chunk_id in sorted(answer.chunk_ids,key=ranks.__getitem__))
            return AnsweredQuestion(answer.answer,answer.status,ordered,answer.trace)
