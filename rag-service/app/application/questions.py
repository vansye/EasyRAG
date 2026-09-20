"""Compose one model session, QA, citation resolution, and completed-answer storage."""

import json
import logging
from collections.abc import Callable, Generator
from contextlib import contextmanager, closing
from dataclasses import asdict, dataclass
from datetime import datetime
from time import perf_counter
from threading import Lock

from app.modules.answer_models.public import Models, ModelUnavailable
from app.modules.knowledge.public import DatabaseUnavailable, HistoryWrite, Knowledge, Source
from app.modules.qa.public import Evidence, Qa, QaError, QaTraceEntry, StreamCancelled, validate_question
from app.modules.retrieval.public import Retrieval

from .errors import GateBusy, QuestionFailed, RecoveryFailed
from .gate import Gate, Operation


logger = logging.getLogger(__name__)


class StreamControl:
    """Serialize disconnect and the one transition into history commit."""

    def __init__(self):
        self._lock = Lock()
        self._cancelled = False
        self._committing = False

    def cancel(self):
        with self._lock:
            self._cancelled = True

    def cancelled(self):
        with self._lock:
            return self._cancelled

    def check(self):
        if self.cancelled():
            raise StreamCancelled()

    def begin_commit(self):
        with self._lock:
            if self._cancelled:
                raise StreamCancelled()
            if self._committing:
                raise RuntimeError('history commit already started')
            self._committing = True


@dataclass(frozen=True)
class QuestionEvent:
    kind: str
    data: object


class _Search:
    def __init__(self, retrieval: Retrieval, record_timing: Callable[[str, float], None]):
        self._retrieval = retrieval
        self._record_timing = record_timing

    def search(self, query: str, top_k: int = 5) -> tuple[Evidence, ...]:
        return tuple(Evidence(hit.chunk_id,hit.document_id,hit.text,hit.heading_path,hit.score)
                     for hit in self._retrieval.search(query,top_k=top_k,record_timing=self._record_timing))


@contextmanager
def _measure(stage: str, record_timing: Callable[[str, float], None]):
    started = perf_counter()
    try:
        yield
    finally:
        record_timing(stage, (perf_counter() - started) * 1000)


@dataclass(frozen=True)
class AnsweredQuestion:
    answer: str
    status: str
    sources: tuple[Source, ...]
    trace: tuple[QaTraceEntry, ...]
    history_id: int
    created_at: datetime
    model: dict[str, str]
    elapsed_ms: int


class Questions:
    def __init__(self, knowledge: Knowledge, retrieval: Retrieval, models: Models, gate: Gate, *, qa: Qa | None = None):
        self._knowledge,self._retrieval,self._models,self._gate = knowledge,retrieval,models,gate
        self._qa = qa if qa is not None else Qa()

    def stream(self, question: str, control: StreamControl) -> Generator[QuestionEvent, None, None]:
        validate_question(question)
        control.check()
        admission = self._gate.try_acquire(Operation.QUERY)
        if admission.lease is None:
            raise GateBusy(admission.state)
        with admission.lease:
            started = perf_counter()
            timings = dict.fromkeys(('session','embedding','vector','judge','generate','sources','history'))
            status, stage, first_text_ms = 'FAILED', 'session', None
            def record_timing(name, milliseconds):
                timings[name] = (timings[name] or 0.0) + milliseconds
            try:
                control.check()
                with _measure('session', record_timing):
                    session = self._models.open_session()
                control.check()
                ordered = ()
                stage = 'search'
                with closing(self._qa.stream(
                    question, _Search(self._retrieval, record_timing), session,
                    record_timing=record_timing, cancelled=control.cancelled,
                )) as events:
                    for event in events:
                        control.check()
                        if event.kind == 'sources':
                            stage = 'sources'
                            with _measure('sources', record_timing):
                                sources = self._knowledge.sources(event.chunk_ids)
                            control.check()
                            by_id = {source.chunk_id: source for source in sources}
                            ranks = {hit.chunk_id: hit.rank for hit in event.trace[-1].retrieved}
                            if set(by_id) != set(event.chunk_ids) or not set(event.chunk_ids) <= ranks.keys():
                                self._gate.require_recovery()
                                raise RecoveryFailed('MISSING_CITATION_SOURCE')
                            ordered = tuple(by_id[key] for key in sorted(event.chunk_ids, key=ranks.__getitem__))
                            yield QuestionEvent('sources', ordered)
                            stage = 'generate'
                        elif event.kind == 'delta':
                            if first_text_ms is None and event.text.strip():
                                first_text_ms = (perf_counter() - started) * 1000
                            yield QuestionEvent('delta', event.text)
                        else:
                            answer = event.draft
                            model = session.model_info
                            elapsed_ms = int((perf_counter() - started) * 1000)
                            stage = 'history'
                            control.begin_commit()
                            with _measure('history', record_timing):
                                history = self._knowledge.save_history(HistoryWrite(
                                    question=question, answer=answer.answer, status=answer.status,
                                    elapsed_ms=elapsed_ms, model=model, sources=ordered,
                                    trace=tuple(asdict(entry) for entry in answer.trace),
                                ))
                            status = answer.status
                            yield QuestionEvent('done', AnsweredQuestion(
                                answer.answer, answer.status, ordered, answer.trace,
                                history.id, history.created_at, model, elapsed_ms,
                            ))
            except (StreamCancelled, GeneratorExit):
                if status == 'FAILED':
                    status = 'CANCELLED'
                raise
            except (ModelUnavailable, QaError, DatabaseUnavailable) as failure:
                stage = getattr(failure, 'stage', stage)
                logger.warning('question_failed cause=%s stage=%s', type(failure).__name__, stage)
                raise QuestionFailed() from None
            finally:
                report = {f'{name}_ms': round(value, 3) if value is not None else None
                          for name, value in timings.items()}
                report.update(status=status, stream=True, failed_stage=stage if status == 'FAILED' else None,
                              first_text_ms=round(first_text_ms, 3) if first_text_ms is not None else None,
                              total_ms=round((perf_counter() - started) * 1000, 3))
                logger.info('question_timing %s', json.dumps(report, separators=(',', ':')))

    def ask(self, question: str) -> AnsweredQuestion:
        validate_question(question)
        admission = self._gate.try_acquire(Operation.QUERY)
        if admission.lease is None:
            raise GateBusy(admission.state)
        with admission.lease:
            started = perf_counter()
            timings: dict[str, float | None] = dict.fromkeys(
                ('session','embedding','vector','judge','generate','sources','history'),
            )
            status, stage = 'FAILED', 'session'

            def record_timing(name: str, milliseconds: float) -> None:
                timings[name] = (timings[name] or 0.0) + milliseconds

            try:
                with _measure('session',record_timing):
                    session = self._models.open_session()
                stage = 'search'
                answer = self._qa.answer(question,_Search(self._retrieval,record_timing),session,
                                         record_timing=record_timing)
                stage = 'sources'
                sources = ()
                if answer.chunk_ids:
                    with _measure('sources',record_timing):
                        sources = self._knowledge.sources(answer.chunk_ids)
                by_id = {source.chunk_id:source for source in sources}
                ranks = {hit.chunk_id:hit.rank for hit in answer.trace[-1].retrieved} if answer.trace else {}
                if set(by_id) != set(answer.chunk_ids) or not set(answer.chunk_ids) <= ranks.keys():
                    self._gate.require_recovery()
                    raise RecoveryFailed('MISSING_CITATION_SOURCE')
                ordered = tuple(by_id[chunk_id] for chunk_id in sorted(answer.chunk_ids,key=ranks.__getitem__))
                model = session.model_info
                elapsed_ms = int((perf_counter() - started) * 1000)
                stage = 'history'
                with _measure('history',record_timing):
                    history = self._knowledge.save_history(HistoryWrite(
                        question=question, answer=answer.answer, status=answer.status, elapsed_ms=elapsed_ms,
                        model=model, sources=ordered, trace=tuple(asdict(entry) for entry in answer.trace),
                    ))
                status = answer.status
                return AnsweredQuestion(answer.answer,answer.status,ordered,answer.trace,
                                        history.id,history.created_at,model,elapsed_ms)
            except (ModelUnavailable,QaError,DatabaseUnavailable) as failure:
                stage = getattr(failure,'stage',stage)
                logger.warning('question_failed cause=%s stage=%s', type(failure).__name__,stage)
                raise QuestionFailed() from None
            finally:
                report = {f'{name}_ms': round(value,3) if value is not None else None
                          for name,value in timings.items()}
                report.update(status=status,failed_stage=stage if status == 'FAILED' else None,
                              total_ms=round((perf_counter()-started)*1000,3))
                logger.info('question_timing %s',json.dumps(report,separators=(',',':')))
