"""One frozen model session per question; citation ranks resolve to MySQL sources."""

from dataclasses import asdict
from datetime import datetime
import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.application.errors import GateBusy, QuestionFailed, RecoveryFailed
from app.application.gate import Gate,Operation,State
from app.application.questions import Questions
from app.modules.answer_models.public import Models,ModelUnavailable
from app.modules.knowledge.public import Knowledge,Source,DatabaseUnavailable
from app.modules.qa.public import AnswerDraft,QaTraceEntry,TraceHit
from app.modules.retrieval.public import Retrieval,SearchHit


@pytest.fixture
def workflow():
    gate=Gate()
    gate.try_acquire(Operation.RECOVERY).lease.confirm_completion()
    a,b,models=Mock(spec=Knowledge),Mock(spec=Retrieval),Mock(spec=Models)
    session=Mock()
    session.model_info={'provider':'openai','model':'frozen-model'}
    a.save_history=Mock(return_value=SimpleNamespace(id=41,created_at=datetime(2026,9,15,12,30)))
    session.complete.side_effect=['{"verdict":"SUFFICIENT"}','First [1], second [2].']
    models.open_session.return_value=session
    b.search.return_value=(SearchHit(30,3,'First','',0.9),SearchHit(10,1,'Second','',0.8))
    sources=(Source(10,1,'Second note','Second',0,6,''),Source(30,3,'First note','First',0,5,''))
    a.sources.return_value=sources
    return SimpleNamespace(a=a,b=b,models=models,session=session,gate=gate,sources=sources,
                           questions=Questions(a,b,models,gate))


def test_one_session_and_trace_rank_resolve_to_the_correct_source(workflow):
    w=workflow
    result=w.questions.ask('Question?')
    assert result.status == 'ANSWERED' and result.answer == 'First [1], second [2].'
    w.models.open_session.assert_called_once_with()
    assert w.session.complete.call_count == 2
    w.a.sources.assert_called_once_with((10,30))
    assert [source.chunk_id for source in result.sources] == [30,10]
    assert result.trace[-1].retrieved[0].chunk_id == result.sources[0].chunk_id
    assert result.history_id == 41 and result.created_at == datetime(2026,9,15,12,30)
    assert result.model == {'provider':'openai','model':'frozen-model'}
    saved=w.a.save_history.call_args.args[0]
    assert saved.question == 'Question?' and saved.answer == result.answer and saved.status == result.status
    assert saved.sources == result.sources and saved.trace == tuple(asdict(entry) for entry in result.trace)
    assert saved.model == result.model and saved.elapsed_ms == result.elapsed_ms >= 0
    w.a.save_history.assert_called_once()
    w.models.get.assert_not_called()
    assert w.gate.state == State.READY


def test_refusal_has_trace_but_does_not_read_sources_or_generate(workflow):
    w=workflow
    w.session.complete.side_effect=['{"verdict":"NONE"}']
    result=w.questions.ask('Question?')
    assert result.status == 'REFUSED' and result.sources == ()
    assert w.session.complete.call_count == 1
    w.a.sources.assert_not_called()
    assert w.a.save_history.call_args.args[0].status == 'REFUSED'
    assert w.a.save_history.call_args.args[0].sources == ()
    assert w.gate.state == State.READY


def test_invalid_input_and_busy_gate_do_not_create_sessions(workflow):
    w=workflow
    with pytest.raises(ValueError):
        w.questions.ask(' \t')
    with w.gate.try_acquire(Operation.MUTATION).lease:
        with pytest.raises(GateBusy):
            w.questions.ask('Question?')
    w.models.open_session.assert_not_called()
    w.a.save_history.assert_not_called()


@pytest.mark.parametrize('stage', ['session','judge','source','history'])
def test_technical_failure_does_not_turn_into_refusal_or_leak_details(workflow,stage):
    w=workflow
    if stage == 'session':
        w.models.open_session.side_effect=ModelUnavailable()
    elif stage == 'judge':
        w.session.complete.side_effect=RuntimeError('private provider body')
    elif stage == 'source':
        w.a.sources.side_effect=DatabaseUnavailable('private SQL body')
    else:
        w.a.save_history.side_effect=DatabaseUnavailable('private history SQL body')
    with pytest.raises(QuestionFailed) as failure:
        w.questions.ask('Question?')
    assert 'private' not in str(failure.value) and w.gate.state == State.READY
    assert w.a.save_history.call_count == (1 if stage == 'history' else 0)
    assert w.models.open_session.call_count == 1


def test_missing_source_closes_query_admission_for_recovery(workflow):
    w=workflow
    w.a.sources.return_value=(w.sources[0],)
    with pytest.raises(RecoveryFailed):
        w.questions.ask('Question?')
    assert w.gate.state == State.RECOVERY_REQUIRED
    w.a.save_history.assert_not_called()


def test_source_resolution_uses_final_trace_instead_of_an_earlier_round(workflow):
    w=workflow
    engine=Mock()
    engine.answer.return_value=AnswerDraft('Answer [1]','ANSWERED',(10,30),(
        QaTraceEntry(1,'earlier',(TraceHit(10,1,0.9,1),TraceHit(30,3,0.8,2)),'PARTIAL'),
        QaTraceEntry(2,'final',(TraceHit(30,3,0.9,1),TraceHit(10,1,0.8,2)),'SUFFICIENT'),
    ))
    result=Questions(w.a,w.b,w.models,w.gate,qa=engine).ask('Question?')
    assert [source.chunk_id for source in result.sources] == [30,10]


def test_partial_answer_saves_coverage_and_original_question(workflow):
    w=workflow
    w.session.complete.side_effect=['{"verdict":"PARTIAL"}','Only the first part is covered [1].']
    result=w.questions.ask('  Original question?  ')
    saved=w.a.save_history.call_args.args[0]
    assert result.status == saved.status == 'PARTIAL'
    assert saved.question == '  Original question?  '
    assert saved.trace[-1]['decision'] == 'PARTIAL'


def test_elapsed_time_covers_model_and_sources_before_history_write(workflow,monkeypatch):
    now=[20.0]
    monkeypatch.setattr('app.application.questions.perf_counter',lambda:now[0])
    def sources(chunk_ids):
        now[0]=21.75
        return workflow.sources
    def save(record):
        now[0]=22.25
        return SimpleNamespace(id=41,created_at=datetime(2026,9,15,12,30))
    workflow.a.sources.side_effect=sources
    workflow.a.save_history.side_effect=save
    result=workflow.questions.ask('Question?')
    assert result.elapsed_ms == workflow.a.save_history.call_args.args[0].elapsed_ms == 1750


@pytest.mark.parametrize('answer', ['Uncited answer', 'Wrong [99]', 'Mixed [1] and [99]', '`array[1]`'])
def test_invalid_generated_citations_do_not_resolve_sources_or_save_history(workflow,answer):
    w=workflow
    w.session.complete.side_effect=['{"verdict":"SUFFICIENT"}',answer]
    with pytest.raises(QuestionFailed):
        w.questions.ask('Question?')
    assert w.session.complete.call_count == 2
    w.models.open_session.assert_called_once()
    w.a.sources.assert_not_called()
    w.a.save_history.assert_not_called()
    assert w.gate.state == State.READY


def test_empty_evidence_saves_a_refusal_without_any_model_completion(workflow):
    w=workflow
    w.b.search.return_value=()
    result=w.questions.ask('No evidence')
    assert result.status == 'REFUSED' and result.sources == ()
    w.session.complete.assert_not_called()
    w.a.sources.assert_not_called()
    w.a.save_history.assert_called_once()
    assert w.a.save_history.call_args.args[0].status == 'REFUSED'
    assert w.gate.state == State.READY


def _timing_reports(caplog):
    return [json.loads(record.getMessage().removeprefix('question_timing '))
            for record in caplog.records if record.getMessage().startswith('question_timing ')]


def test_stage_timing_includes_history_commit_and_isolates_each_question(workflow,monkeypatch,caplog):
    w=workflow
    now=[10.0]
    monkeypatch.setattr('app.application.questions.perf_counter',lambda:now[0])
    monkeypatch.setattr('app.modules.qa.public.perf_counter',lambda:now[0],raising=False)
    calls=[0]
    def session():
        now[0]+=0.125
        return w.session
    def search(query,top_k=5,*,record_timing=None):
        for stage,duration in [('vector',0.025),('embedding',0.25),('vector',0.05)]:
            now[0]+=duration
            if record_timing:
                record_timing(stage,duration*1000)
        return w.b.search.return_value
    def complete(prompt):
        index=calls[0]%2
        calls[0]+=1
        now[0]+=[0.5,1.5][index]
        return ['{"verdict":"SUFFICIENT"}','First [1], second [2].'][index]
    def sources(chunk_ids):
        now[0]+=0.05
        return w.sources
    def save(record):
        now[0]+=0.1
        return SimpleNamespace(id=41,created_at=datetime(2026,9,15,12,30))
    w.models.open_session.side_effect=session
    w.b.search.side_effect=search
    w.session.complete.side_effect=complete
    w.a.sources.side_effect=sources
    w.a.save_history.side_effect=save
    with caplog.at_level(logging.INFO,logger='app.application.questions'):
        for _ in range(2):
            result=w.questions.ask('private question containing private-api-key')
            assert result.elapsed_ms == pytest.approx(2500,abs=1)
    reports=_timing_reports(caplog)
    assert len(reports) == 2
    for report in reports:
        assert report['status'] == 'ANSWERED'
        assert report['failed_stage'] is None
        assert {key:report[key] for key in (
            'session_ms','embedding_ms','vector_ms','judge_ms','generate_ms','sources_ms','history_ms','total_ms'
        )} == pytest.approx(dict(session_ms=125,embedding_ms=250,vector_ms=75,judge_ms=500,
                               generate_ms=1500,sources_ms=50,history_ms=100,total_ms=2600))
    assert 'private' not in caplog.text
    assert w.session.complete.call_count == 4


def test_empty_evidence_timing_marks_skipped_stages_without_inventing_model_work(workflow,caplog):
    w=workflow
    w.b.search.return_value=()
    with caplog.at_level(logging.INFO,logger='app.application.questions'):
        w.questions.ask('private question')
    reports=_timing_reports(caplog)
    assert len(reports) == 1
    report=reports[0]
    assert report['status'] == 'REFUSED'
    assert report['judge_ms'] is report['generate_ms'] is report['sources_ms'] is None
    assert report['history_ms'] >= 0 and report['total_ms'] >= report['history_ms']
    w.session.complete.assert_not_called()


@pytest.mark.parametrize('stage',['judge','generate','sources','history'])
def test_failed_question_records_timing_and_releases_admission(workflow,monkeypatch,caplog,stage):
    w=workflow
    now=[10.0]
    monkeypatch.setattr('app.application.questions.perf_counter',lambda:now[0])
    monkeypatch.setattr('app.modules.qa.public.perf_counter',lambda:now[0],raising=False)
    def fail(*args):
        now[0]+=0.25
        if stage in ('judge','generate'):
            raise RuntimeError('private provider body and private-api-key')
        raise DatabaseUnavailable('private SQL body')
    if stage in ('judge','generate'):
        if stage == 'generate':
            def complete(prompt):
                if w.session.complete.call_count == 1:
                    return '{"verdict":"SUFFICIENT"}'
                return fail()
            w.session.complete.side_effect=complete
        else:
            w.session.complete.side_effect=fail
    else:
        getattr(w.a,'sources' if stage == 'sources' else 'save_history').side_effect=fail
    with caplog.at_level(logging.INFO,logger='app.application.questions'):
        with pytest.raises(QuestionFailed):
            w.questions.ask('private question')
    reports=_timing_reports(caplog)
    assert len(reports) == 1
    report=reports[0]
    assert report['status'] == 'FAILED' and report['failed_stage'] == stage
    assert report[f'{stage}_ms'] == 250 and report['total_ms'] == 250
    assert 'private' not in caplog.text
    assert w.gate.state == State.READY
