"""Single indexing executor and document workflow, composed from public ports."""

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock

from app.modules.knowledge.public import ChunkWrite, DocumentNotFound, IndexStateConflict, Knowledge
from app.modules.retrieval.public import IndexChunk, IndexWriteError, Retrieval, RetrievalUnavailable

from .gate import Gate, Lease, Operation


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IndexResult:
    document_id: int
    outcome: str
    error: str | None = None


def _reason(stage, failure):
    detail = type(failure).__name__
    if isinstance(failure, RetrievalUnavailable):
        detail += f' ({failure.component}/{failure.cause})'
    elif isinstance(failure, IndexWriteError):
        detail += f' ({failure.cause}; cleanup={failure.cleanup_error})'
    return f'{stage}: {detail}'


class Indexer:
    def __init__(self, knowledge: Knowledge, retrieval: Retrieval, gate: Gate):
        self._knowledge, self._retrieval, self._gate = knowledge, retrieval, gate

    def run(self, document_id: int, lease: Lease | None = None) -> IndexResult:
        if lease is None:
            lease = self._gate.try_acquire(Operation.MUTATION).lease
            if lease is None:
                return IndexResult(document_id, 'BUSY')
        elif not self._gate.owns(lease, Operation.MUTATION):
            raise ValueError('indexing requires this gate\'s active mutation lease')
        known_pending, stage = False, 'LOAD_DOCUMENT'
        with lease:
            try:
                try:
                    document = self._knowledge.get(document_id)
                except DocumentNotFound:
                    lease.confirm_completion()
                    return IndexResult(document_id, 'SKIPPED')
                if document.index_status == 'INDEXING':
                    raise IndexStateConflict('orphan indexing state requires offline recovery')
                if document.index_status != 'PENDING':
                    lease.confirm_completion()
                    return IndexResult(document_id, 'SKIPPED')
                known_pending = True
                stage = 'SPLIT'
                drafts = self._retrieval.split(document.content, document.title)
                writes = tuple(ChunkWrite(seq, draft.text, draft.byte_start, draft.byte_end,
                                          draft.heading_path, draft.token_count) for seq, draft in enumerate(drafts))
                stage = 'SAVE_CHUNKS'
                chunks = self._knowledge.begin_indexing(document_id, writes, expected_content=document.content)
                inputs = tuple(IndexChunk(chunk.id, chunk.text, chunk.heading_path, document.tags) for chunk in chunks)
                stage = 'REPLACE_INDEX'
                if self._retrieval.replace(document_id, inputs) != len(chunks):
                    raise RuntimeError('indexed count mismatch')
                stage = 'MARK_INDEXED'
                self._knowledge.mark_indexed(document_id)
                lease.confirm_completion()
                return IndexResult(document_id, 'INDEXED')
            except Exception as failure:
                reason = _reason(stage, failure)
                cleanup_confirmed = terminal_confirmed = False
                if known_pending:
                    try:
                        self._retrieval.delete_document(document_id)
                        cleanup_confirmed = True
                    except Exception as cleanup_failure:
                        reason += '; ' + _reason('DELETE_INDEX', cleanup_failure)
                    try:
                        self._knowledge.mark_failed(document_id, reason[:1024])
                        terminal_confirmed = True
                    except Exception as terminal_failure:
                        logger.warning('indexing_terminal_unconfirmed document_id=%s cause=%s',
                                       document_id, type(terminal_failure).__name__)
                if cleanup_confirmed and terminal_confirmed:
                    lease.confirm_completion()
                logger.warning('indexing_failed document_id=%s stage=%s cause=%s cleanup_confirmed=%s terminal_confirmed=%s',
                               document_id, stage, type(failure).__name__, cleanup_confirmed, terminal_confirmed)
                return IndexResult(document_id, 'FAILED', reason)


class QueueClosed(RuntimeError):
    pass


class IndexingQueue:
    def __init__(self, indexer: Indexer):
        self._indexer = indexer
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='knowledge-index')
        self._lock = Lock()
        self._accepting = True

    def submit(self, document_id: int, lease: Lease | None = None) -> Future:
        with self._lock:
            if not self._accepting:
                raise QueueClosed('indexing executor has stopped accepting tasks')
            return self._executor.submit(self._indexer.run, document_id, lease)

    def close(self):
        with self._lock:
            self._accepting = False
        self._executor.shutdown(wait=True)
