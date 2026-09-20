"""Only a complete, valid answer may produce a final streaming draft."""

from types import SimpleNamespace

import pytest

from app.modules.qa import public as qa


def inputs(verdict='SUFFICIENT', parts=('Answer [', '1].')):
    calls = []
    def complete(prompt):
        calls.append('judge')
        return '{"verdict":"' + verdict + '"}'
    def stream(prompt):
        calls.append('generate')
        try:
            yield from parts
        finally:
            calls.append('closed')
    search = SimpleNamespace(search=lambda *a, **kw: (qa.Evidence(30, 3, 'Evidence', '', .9),))
    return search, SimpleNamespace(complete=complete, stream=stream), calls


@pytest.mark.parametrize('verdict,status', [('SUFFICIENT','ANSWERED'), ('PARTIAL','PARTIAL')])
def test_sources_and_deltas_precede_validated_final(verdict, status):
    search, chat, calls = inputs(verdict)
    stream = qa.Qa().stream('Question', search, chat)
    first = next(stream)
    assert first.kind == 'sources' and first.chunk_ids == (30,)
    assert calls == ['judge']
    assert next(stream).text == 'Answer ['
    assert calls == ['judge','generate']
    assert next(stream).text == '1].'
    final = next(stream)
    assert final.kind == 'done' and final.draft.status == status
    assert final.draft.answer == 'Answer [1].'
    assert list(stream) == [] and calls[-1] == 'closed'


@pytest.mark.parametrize('empty', [True, False])
def test_refusal_never_starts_generation(empty):
    search, chat, calls = inputs('NONE')
    if empty:
        search.search = lambda *a, **kw: ()
    events = list(qa.Qa().stream('Question', search, chat))
    assert len(events) == 1 and events[0].draft.status == 'REFUSED'
    assert calls == ([] if empty else ['judge'])


@pytest.mark.parametrize('parts', [('uncited',), ('bad [99]',), (' ',), (123,)])
def test_invalid_generation_has_no_final(parts):
    search, chat, calls = inputs(parts=parts)
    received = []
    with pytest.raises(qa.QaError):
        for event in qa.Qa().stream('Question', search, chat):
            received.append(event.kind)
    assert 'done' not in received and calls[-1] == 'closed'


@pytest.mark.parametrize('cancel_at', ['search', 'judge', 'delta'])
def test_cancellation_stops_before_next_model_stage(cancel_at):
    search, chat, calls = inputs()
    cancelled = [False]
    original_search, original_complete = search.search, chat.complete
    def find(*a, **kw):
        result = original_search(*a, **kw)
        cancelled[0] = cancel_at == 'search'
        return result
    def complete(prompt):
        result = original_complete(prompt)
        cancelled[0] = cancel_at == 'judge'
        return result
    search.search, chat.complete = find, complete
    with pytest.raises(qa.StreamCancelled):
        for event in qa.Qa().stream('Question', search, chat, cancelled=lambda: cancelled[0]):
            if event.kind == 'delta':
                cancelled[0] = True
    assert calls == {'search': [], 'judge': ['judge'], 'delta': ['judge','generate','closed']}[cancel_at]


def test_consumer_closes_nested_generator():
    search, chat, calls = inputs()
    stream = qa.Qa().stream('Question', search, chat)
    next(stream)
    next(stream)
    stream.close()
    assert calls[-1] == 'closed'
