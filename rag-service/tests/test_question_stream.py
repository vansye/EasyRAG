"""Streaming retains the query lease through validation and atomic history commit."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app.application import questions
from app.application.errors import QuestionFailed, RecoveryFailed
from app.application.gate import Operation, State
from app.modules.qa.public import StreamCancelled
from tests.test_application_questions import workflow


def setup_stream(w, parts=('First [', '1], second [2].')):
    w.session.complete.side_effect = ['{"verdict":"SUFFICIENT"}']
    w.session.stream.side_effect = lambda prompt: (part for part in parts)


def test_ranked_sources_before_deltas_and_history_before_done(workflow):
    w = workflow
    setup_stream(w)
    control = questions.StreamControl()
    stream = w.questions.stream('Question?', control)
    source = next(stream)
    assert source.kind == 'sources' and [s.chunk_id for s in source.data] == [30, 10]
    assert w.gate.state == State.QUERYING
    assert next(stream).data == 'First ['
    w.a.save_history.assert_not_called()
    assert next(stream).data == '1], second [2].'
    done = next(stream)
    assert done.kind == 'done' and done.data.history_id == 41
    assert done.data.sources == source.data
    w.a.save_history.assert_called_once()
    assert list(stream) == [] and w.gate.state == State.READY


@pytest.mark.parametrize('failure', ['citations', 'source', 'cancel'])
def test_failure_or_cancel_never_saves_partial_history(workflow, failure):
    w = workflow
    setup_stream(w, ('uncited',) if failure == 'citations' else ('Answer [1]',))
    if failure == 'source':
        w.a.sources.return_value = ()
    control = questions.StreamControl()
    expected = {'citations':QuestionFailed, 'source':RecoveryFailed, 'cancel':StreamCancelled}[failure]
    with pytest.raises(expected):
        for event in w.questions.stream('Question?', control):
            if event.kind == 'delta' and failure == 'cancel':
                control.cancel()
    w.a.save_history.assert_not_called()
    assert w.gate.state == (State.RECOVERY_REQUIRED if failure == 'source' else State.READY)


def test_cancellation_during_blocked_model_keeps_lease_until_worker_exits(workflow):
    w = workflow
    setup_stream(w)
    entered, release, closed = Event(), Event(), Event()
    def generate(prompt):
        try:
            entered.set()
            assert release.wait(5)
            yield 'Answer [1]'
        finally:
            closed.set()
    w.session.stream.side_effect = generate
    control = questions.StreamControl()
    with ThreadPoolExecutor() as pool:
        job = pool.submit(lambda: list(w.questions.stream('Question?', control)))
        try:
            assert entered.wait(5)
            control.cancel()
            assert w.gate.try_acquire(Operation.MUTATION).lease is None
            w.a.save_history.assert_not_called()
        finally:
            release.set()
        with pytest.raises(StreamCancelled):
            job.result(5)
    assert closed.is_set() and w.gate.state == State.READY


def test_cancel_after_commit_begins_allows_one_atomic_save(workflow):
    w = workflow
    setup_stream(w)
    control = questions.StreamControl()
    result = w.a.save_history.return_value
    def save(record):
        control.cancel()
        return result
    w.a.save_history.side_effect = save
    events = list(w.questions.stream('Question?', control))
    assert events[-1].kind == 'done'
    w.a.save_history.assert_called_once()
    assert w.gate.state == State.READY


def test_cancel_before_commit_prevents_commit_transition():
    control = questions.StreamControl()
    control.cancel()
    with pytest.raises(StreamCancelled):
        control.begin_commit()


def test_refusal_saves_without_sources_or_generation(workflow):
    w = workflow
    w.b.search.return_value = ()
    events = list(w.questions.stream('Question?', questions.StreamControl()))
    assert len(events) == 1 and events[0].data.status == 'REFUSED'
    w.a.sources.assert_not_called()
    w.session.complete.assert_not_called()
    w.session.stream.assert_not_called()
