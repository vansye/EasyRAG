"""G composes public module contracts without holding a database transaction."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.application.gate import Gate, Operation, State
from app.application.indexing import Indexer, IndexingQueue, QueueClosed
from app.modules.knowledge import public as knowledge
from app.modules.retrieval import public as retrieval


@pytest.fixture
def workflow():
    gate = Gate()
    gate.try_acquire(Operation.RECOVERY).lease.confirm_completion()
    a, b = Mock(spec=knowledge.Knowledge), Mock(spec=retrieval.Retrieval)
    record = SimpleNamespace(id=1, content='# Title\n猫🙂', title='Title', tags=('notes',), index_status='PENDING')
    a.get.return_value = record
    draft = retrieval.ChunkDraft(record.content, 'Title', 0, len(record.content.encode()), 4)
    b.split.return_value = (draft,)
    a.begin_indexing.return_value = (knowledge.StoredChunk(101, 1, 0, draft.text, 0, draft.byte_end, 'Title', 4),)
    b.replace.return_value = 1
    calls = Mock()
    for name, target in [('get',a.get),('split',b.split),('begin',a.begin_indexing),('replace',b.replace),
                         ('indexed',a.mark_indexed),('delete',b.delete_document),('failed',a.mark_failed)]:
        calls.attach_mock(target, name)
    return SimpleNamespace(gate=gate, a=a, b=b, record=record, calls=calls, indexer=Indexer(a,b,gate))


def test_indexing_orders_owned_operations_and_confirms_only_terminal_state(workflow):
    w = workflow
    def check_replace(document_id, chunks):
        assert w.gate.state == State.MUTATING
        assert not w.gate.try_acquire(Operation.QUERY).lease
        assert chunks == (retrieval.IndexChunk(101, w.record.content, 'Title', ('notes',)),)
        return 1
    w.b.replace.side_effect = check_replace
    result = w.indexer.run(1)
    assert result.outcome == 'INDEXED' and w.gate.state == State.READY
    assert [call[0] for call in w.calls.mock_calls] == ['get','split','begin','replace','indexed']
    assert w.a.begin_indexing.call_args.kwargs['expected_content'] == w.record.content
    assert w.a.begin_indexing.call_args.args[1] == (knowledge.ChunkWrite(0, w.record.content, 0, len(w.record.content.encode()), 'Title', 4),)


@pytest.mark.parametrize('state', ['INDEXED','FAILED'])
def test_finished_documents_are_skipped_without_index_mutation(workflow, state):
    w = workflow
    w.record.index_status = state
    assert w.indexer.run(1).outcome == 'SKIPPED'
    assert w.gate.state == State.READY
    w.b.split.assert_not_called()
    w.b.delete_document.assert_not_called()


def test_missing_document_is_skipped_but_orphan_indexing_requires_recovery(workflow):
    w = workflow
    w.a.get.side_effect = knowledge.DocumentNotFound()
    assert w.indexer.run(1).outcome == 'SKIPPED' and w.gate.state == State.READY
    w.a.get.side_effect = None
    w.record.index_status = 'INDEXING'
    assert w.indexer.run(1).outcome == 'FAILED' and w.gate.state == State.RECOVERY_REQUIRED
    w.b.delete_document.assert_not_called()
    w.a.mark_failed.assert_not_called()


@pytest.mark.parametrize('step,stage', [('split','SPLIT'),('begin','SAVE_CHUNKS'),('replace','REPLACE_INDEX'),('indexed','MARK_INDEXED')])
def test_known_pending_failure_cleans_once_and_records_terminal_state(workflow, step, stage):
    w = workflow
    getattr(w.calls, step).side_effect = RuntimeError('private upstream payload')
    result = w.indexer.run(1)
    assert result.outcome == 'FAILED' and w.gate.state == State.READY
    w.b.delete_document.assert_called_once_with(1)
    w.a.mark_failed.assert_called_once()
    diagnostic = w.a.mark_failed.call_args.args[1]
    assert stage in diagnostic and 'RuntimeError' in diagnostic and 'private' not in diagnostic


def test_unconfirmed_load_cannot_delete_or_reopen_gate(workflow):
    w = workflow
    w.a.get.side_effect = knowledge.DatabaseUnavailable('unconfirmed')
    assert w.indexer.run(1).outcome == 'FAILED'
    w.b.delete_document.assert_not_called()
    w.a.mark_failed.assert_not_called()
    assert w.gate.state == State.RECOVERY_REQUIRED


@pytest.mark.parametrize('uncertain', ['cleanup','terminal'])
def test_cleanup_or_database_terminal_failure_keeps_recovery_required(workflow, uncertain):
    w = workflow
    w.b.replace.side_effect = retrieval.IndexWriteError('WRITE_FAILED', 'FIRST_CLEANUP_FAILED')
    if uncertain == 'cleanup':
        w.b.delete_document.side_effect = retrieval.RetrievalUnavailable('index','DELETE_FAILED')
    else:
        w.a.mark_failed.side_effect = knowledge.DatabaseUnavailable('unconfirmed')
    result = w.indexer.run(1)
    assert result.outcome == 'FAILED' and w.gate.state == State.RECOVERY_REQUIRED
    w.b.delete_document.assert_called_once_with(1)


def test_confirmed_retry_cleanup_can_finish_a_failed_replace(workflow):
    w = workflow
    w.b.replace.side_effect = retrieval.IndexWriteError('WRITE_FAILED', 'FIRST_CLEANUP_FAILED')
    assert w.indexer.run(1).outcome == 'FAILED' and w.gate.state == State.READY
    assert 'FIRST_CLEANUP_FAILED' in w.a.mark_failed.call_args.args[1]


def test_replace_count_mismatch_is_cleaned_before_reopening(workflow):
    w = workflow
    w.b.replace.return_value = 0
    assert w.indexer.run(1).outcome == 'FAILED' and w.gate.state == State.READY
    w.a.mark_indexed.assert_not_called()
    w.b.delete_document.assert_called_once_with(1)


def test_repeated_chunks_are_all_stored_but_indexed_once(workflow):
    w = workflow
    drafts = tuple(retrieval.ChunkDraft(text, 'Title', start, start + 3, 1)
                   for start, text in ((0, 'dup'), (3, 'one'), (6, 'dup')))
    w.b.split.return_value = drafts
    w.a.begin_indexing.return_value = tuple(
        knowledge.StoredChunk(101 + seq, 1, seq, d.text, d.byte_start, d.byte_end, 'Title', 1) for seq, d in enumerate(drafts))
    w.b.replace.return_value = 2
    assert w.indexer.run(1).outcome == 'INDEXED' and w.gate.state == State.READY
    assert len(w.a.begin_indexing.call_args.args[1]) == 3
    w.b.replace.assert_called_once_with(1, (
        retrieval.IndexChunk(101, 'dup', 'Title', ('notes',)), retrieval.IndexChunk(102, 'one', 'Title', ('notes',))))
    w.b.replace.return_value = 3
    w.record.index_status = 'PENDING'
    assert w.indexer.run(1).outcome == 'FAILED'
    w.a.mark_indexed.assert_called_once()


def test_busy_worker_leaves_pending_without_waiting_or_touching_modules(workflow):
    w = workflow
    with w.gate.try_acquire(Operation.QUERY).lease:
        assert w.indexer.run(1).outcome == 'BUSY'
    w.a.get.assert_not_called()
    assert w.record.index_status == 'PENDING'


def test_queued_unleased_work_cannot_deadlock_a_later_leased_task(workflow):
    w = workflow
    lease = w.gate.try_acquire(Operation.MUTATION).lease
    queue = IndexingQueue(w.indexer)
    try:
        ordinary = queue.submit(2)
        transferred = queue.submit(1, lease)
        assert ordinary.result(timeout=3).outcome == 'BUSY'
        assert transferred.result(timeout=3).outcome == 'INDEXED'
        assert w.gate.state == State.READY
        w.a.get.assert_called_once_with(1)
    finally:
        queue.close()


def test_rejects_a_query_or_foreign_lease_before_any_module_call(workflow):
    w = workflow
    with w.gate.try_acquire(Operation.QUERY).lease as lease:
        with pytest.raises(ValueError):
            w.indexer.run(1, lease)
    w.a.get.assert_not_called()


def test_queue_shutdown_waits_for_inflight_work_and_does_not_release_its_lease(workflow):
    w = workflow
    entered, release = Event(), Event()
    def blocking_replace(*_):
        entered.set()
        assert release.wait(5)
        return 1
    w.b.replace.side_effect = blocking_replace
    queue = IndexingQueue(w.indexer)
    task = queue.submit(1)
    try:
        assert entered.wait(3)
        with ThreadPoolExecutor(max_workers=1) as closer:
            shutdown = closer.submit(queue.close)
            try:
                assert not shutdown.done() and w.gate.state == State.MUTATING
            finally:
                release.set()
            shutdown.result(timeout=3)
        assert task.result().outcome == 'INDEXED' and w.gate.state == State.READY
        with pytest.raises(QueueClosed):
            queue.submit(1)
    finally:
        release.set()
        queue.close()
