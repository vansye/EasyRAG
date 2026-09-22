"""C owns answer decisions and citations; its ports keep these tests offline."""

from __future__ import annotations

import json
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, asdict
from threading import Barrier

import pytest

from app.modules.qa import public as qa


class _FixedSearch:
    def __init__(self, evidence):
        self.evidence = evidence
        self.calls = []

    def search(self, query, top_k=5):
        self.calls.append((query, top_k))
        return self.evidence


class _ScriptedChat:
    def __init__(self, *replies):
        self.replies = iter(replies)
        self.prompts = []

    def complete(self, prompt):
        self.prompts.append(prompt)
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _evidence():
    return (
        qa.Evidence(303, 12, "Redis 保留内存中的数据。", "Redis / 内存", 0.61),
        qa.Evidence(101, 11, "Redis 支持持久化。", "Redis / 持久化", 0.94),
        qa.Evidence(303, 12, "Redis 支持多种数据结构。", "Redis / 数据结构", 0.48),
    )


_JUDGE_PROMPT = (
    "你是知识库问答的判定器。根据下面的检索片段判断：仅凭这些内容，"
    "能否完整回答用户的问题？\n\n"
    "问题：Redis 有哪些特点？\n\n检索片段：\n"
    "[1] Redis 保留内存中的数据。\n\n"
    "[2] Redis 支持持久化。\n\n"
    "[3] Redis 支持多种数据结构。\n\n"
    "判定规则：\n"
    "- SUFFICIENT：片段中有与问题主题直接相关的内容，且足以支撑完整回答\n"
    "- PARTIAL：有直接相关内容，但只覆盖问题的一部分，缺失的是问题主体而非边角\n"
    "- NONE：没有与问题主题直接相关的片段（词面相似不算直接相关）\n\n"
    "同时列出与问题主题直接相关、支撑该判定的片段编号 relevant：回答时只能看到并引用这些片段，"
    "漏掉的片段无法再被引用；SUFFICIENT / PARTIAL 至少列一个，NONE 为空数组。\n"
    '只输出 JSON：{"verdict": "SUFFICIENT", "relevant": [1, 3]}（verdict 三选一），不要输出其他内容。'
)

_GENERATE_PROMPT = (
    "根据下面的知识库片段回答问题。要求：\n"
    "1. 只使用片段中出现的信息，不得编造或补充片段之外的知识\n"
    "2. 每个事实性结论后用 [n] 标注来源片段编号，编号只能取片段前标注的编号\n\n"
    "问题：Redis 有哪些特点？\n\n片段：\n"
    "[1] Redis 保留内存中的数据。\n\n"
    "[2] Redis 支持持久化。\n\n"
    "[3] Redis 支持多种数据结构。"
)


def test_sufficient_preserves_prompts_and_rank_to_chunk_mapping():
    service = qa.Qa()
    search = _FixedSearch(_evidence())
    generated = "  Redis 支持持久化 [2]，并提供多种数据结构 [3]。\n"
    chat = _ScriptedChat('{"verdict": "SUFFICIENT"}', generated)

    draft = service.answer("Redis 有哪些特点？", search, chat, top_k=3)

    assert search.calls == [("Redis 有哪些特点？", 3)]
    assert chat.prompts == [_JUDGE_PROMPT, _GENERATE_PROMPT]
    assert draft.chunk_ids == (101, 303)
    assert json.loads(json.dumps(asdict(draft))) == {
        "answer": generated,
        "status": "ANSWERED",
        "chunk_ids": [101, 303],
        "trace": [{
            "round_index": 1,
            "query": "Redis 有哪些特点？",
            "retrieved": [
                {"chunk_id": 303, "document_id": 12, "score": 0.61, "rank": 1},
                {"chunk_id": 101, "document_id": 11, "score": 0.94, "rank": 2},
                {"chunk_id": 303, "document_id": 12, "score": 0.48, "rank": 3},
            ],
            "decision": "SUFFICIENT",
            "relevant": [1, 2, 3],
        }],
    }


def test_relevant_subset_keeps_original_numbering_and_restricts_generation_and_citations():
    search = _FixedSearch(_evidence())
    chat = _ScriptedChat('{"verdict": "SUFFICIENT", "relevant": [3, 1, 3]}', "内存数据 [1]，多种结构 [3]。")

    draft = qa.Qa().answer("Redis 有哪些特点？", search, chat, top_k=3)

    generate_prompt = chat.prompts[1]
    assert "[1] Redis 保留内存中的数据。\n\n[3] Redis 支持多种数据结构。" in generate_prompt
    assert "[2] Redis 支持持久化。" not in generate_prompt
    assert draft.chunk_ids == (303,)
    # the judge listed [3, 1, 3]; selection keeps candidate order and drops the duplicate
    assert draft.trace[0].relevant == (1, 3)
    assert [hit.rank for hit in draft.trace[0].retrieved] == [1, 2, 3]

    chat = _ScriptedChat('{"verdict": "SUFFICIENT", "relevant": [1]}', "持久化 [2]。")
    with pytest.raises(qa.QaError) as failure:
        qa.Qa().answer("Redis 有哪些特点？", search, chat, top_k=3)
    assert (failure.value.stage, failure.value.cause) == ("generate", "INVALID_CITATIONS")


@pytest.mark.parametrize("reply", [
    '{"verdict": "SUFFICIENT", "relevant": []}',
    '{"verdict": "PARTIAL", "relevant": [0]}',
    '{"verdict": "SUFFICIENT", "relevant": [4]}',
    '{"verdict": "SUFFICIENT", "relevant": ["1"]}',
    '{"verdict": "SUFFICIENT", "relevant": [true]}',
    '{"verdict": "SUFFICIENT", "relevant": 2}',
])
def test_malformed_relevant_is_an_invalid_judgement_and_never_generates(reply):
    chat = _ScriptedChat(reply, "never generated [1]")
    with pytest.raises(qa.QaError) as failure:
        qa.Qa().answer("Redis 有哪些特点？", _FixedSearch(_evidence()), chat, top_k=3)
    assert (failure.value.stage, failure.value.cause) == ("judge", "INVALID_JUDGE_OUTPUT")
    assert len(chat.prompts) == 1


def test_none_ignores_relevant_and_records_no_selection():
    chat = _ScriptedChat('{"verdict": "NONE", "relevant": [1, 2]}')
    draft = qa.Qa().answer("Redis 有哪些特点？", _FixedSearch(_evidence()), chat)
    assert draft.status == "REFUSED" and draft.trace[0].relevant == () and len(chat.prompts) == 1


def test_partial_keeps_one_search_and_the_original_boundary_instruction():
    search = _FixedSearch(_evidence())
    chat = _ScriptedChat('{"verdict": "PARTIAL"}', "库中仅提及 Redis 的部分特点 [1]。")

    draft = qa.Qa().answer("Redis 有哪些特点？", search, chat)

    assert draft.status == "PARTIAL"
    assert draft.answer == "库中仅提及 Redis 的部分特点 [1]。"
    assert draft.chunk_ids == (101, 303)
    assert draft.trace[0].decision == "PARTIAL"
    assert search.calls == [("Redis 有哪些特点？", 5)]
    assert chat.prompts == [_JUDGE_PROMPT, _GENERATE_PROMPT + (
        "\n注意：以上片段只覆盖了问题的一部分。回答时先基于已有内容作答，"
        "并明确声明覆盖边界（说明库中只有这些相关内容，未覆盖的部分无法回答）。"
    )]


@pytest.mark.parametrize("has_evidence", [False, True])
def test_none_refuses_without_generation_and_only_judges_nonempty_evidence(has_evidence):
    evidence = _evidence() if has_evidence else ()
    search = _FixedSearch(evidence)
    chat = _ScriptedChat('{"verdict": "NONE"}')

    draft = qa.Qa().answer("Redis 有哪些特点？", search, chat)

    assert draft.status == "REFUSED"
    assert draft.answer == "知识库中没有找到能回答这个问题的内容。"
    assert draft.chunk_ids == ()
    assert len(chat.prompts) == (1 if has_evidence else 0)
    if has_evidence:
        assert "判定规则：" in chat.prompts[0]
    assert len(draft.trace) == 1
    assert draft.trace[0].decision == "NONE"
    assert tuple(hit.chunk_id for hit in draft.trace[0].retrieved) == tuple(
        item.chunk_id for item in evidence
    )


@pytest.mark.parametrize("reply", [
    '```json\n{"verdict": "SUFFICIENT"}\n```',
    '判定结果如下：\n{\n  "verdict": "SUFFICIENT"\n}\n',
])
def test_judge_preserves_json_extraction_from_surrounding_text(reply):
    chat = _ScriptedChat(reply, "回答 [1]。")

    draft = qa.Qa().answer("问题", _FixedSearch(_evidence()), chat)

    assert draft.status == "ANSWERED"
    assert draft.trace[0].decision == "SUFFICIENT"


_PRIVATE_DETAILS = "private-question Authorization: Bearer private-api-key https://private-model.test"


def _assert_safe_error(error, stage, cause):
    assert type(error) is qa.QaError
    assert error.stage == stage
    assert error.cause == cause
    rendered = str(error) + repr(error) + "".join(traceback.format_exception(error))
    assert all(marker not in rendered for marker in (
        "private-question", "Authorization", "private-api-key", "private-model.test",
    ))
    assert error.__cause__ is None


@pytest.mark.parametrize("reply", [
    _PRIVATE_DETAILS,
    json.dumps({"verdict": _PRIVATE_DETAILS}),
    '{"verdict": "NONE", "debug": "private-api-key",}',
    json.dumps({"reason": _PRIVATE_DETAILS}),
    '{"verdict": null}',
    '{"verdict": ["NONE"]}',
    '{"verdict": {"value": "NONE"}}',
])
def test_invalid_judge_output_is_a_sanitized_failure_and_never_generates(reply):
    chat = _ScriptedChat(reply, "must not generate")

    with pytest.raises(RuntimeError) as failure:
        qa.Qa().answer("问题", _FixedSearch(_evidence()), chat)

    _assert_safe_error(failure.value, "judge", "INVALID_JUDGE_OUTPUT")
    assert len(chat.prompts) == 1


@pytest.mark.parametrize("reply", ["", " \n\t", None, ["text block"], 42])
@pytest.mark.parametrize("stage", ["judge", "generate"])
def test_empty_or_nontext_chat_content_is_a_technical_failure(reply, stage):
    replies = (reply,) if stage == "judge" else ('{"verdict": "SUFFICIENT"}', reply)
    chat = _ScriptedChat(*replies)

    with pytest.raises(RuntimeError) as failure:
        qa.Qa().answer("问题", _FixedSearch(_evidence()), chat)

    _assert_safe_error(failure.value, stage, "EMPTY_OR_NON_TEXT_CONTENT")
    assert len(chat.prompts) == (1 if stage == "judge" else 2)


@pytest.mark.parametrize("stage", ["judge", "generate"])
def test_chat_exceptions_are_wrapped_without_upstream_details(stage):
    upstream = RuntimeError(_PRIVATE_DETAILS)
    replies = (upstream,) if stage == "judge" else ('{"verdict": "SUFFICIENT"}', upstream)
    chat = _ScriptedChat(*replies)

    with pytest.raises(RuntimeError) as failure:
        qa.Qa().answer("问题", _FixedSearch(_evidence()), chat)

    _assert_safe_error(failure.value, stage, "RuntimeError")
    assert len(chat.prompts) == (1 if stage == "judge" else 2)


def test_search_failure_is_wrapped_before_any_chat_call():
    class FailingSearch:
        def search(self, query, top_k=5):
            raise RuntimeError(_PRIVATE_DETAILS)

    chat = _ScriptedChat()

    with pytest.raises(RuntimeError) as failure:
        qa.Qa().answer("问题", FailingSearch(), chat)

    _assert_safe_error(failure.value, "search", "RuntimeError")
    assert chat.prompts == []


@pytest.mark.parametrize("question, message", [
    ("", "blank"),
    (" \r\n\t\u3000", "blank"),
    (None, "blank"),
    pytest.param("问" * 2001, "2000 code points", id="too-many-code-points"),
    pytest.param("😀" * 2001, "2000 code points", id="too-many-astral-code-points"),
])
def test_public_question_validation_matches_answer_before_port_calls(question, message):
    search = _FixedSearch(())
    chat = _ScriptedChat('{"verdict": "NONE"}')

    with pytest.raises(ValueError, match=message) as validated:
        qa.validate_question(question)
    with pytest.raises(ValueError, match=message) as answered:
        qa.Qa().answer(question, search, chat)

    assert str(answered.value) == str(validated.value)
    assert search.calls == []
    assert chat.prompts == []


def test_question_limit_counts_code_points_and_preserves_valid_input():
    question = " " + "😀" * 1998 + " "
    search = _FixedSearch(_evidence())
    chat = _ScriptedChat('{"verdict": "NONE"}')

    assert qa.validate_question(question) is None
    draft = qa.Qa().answer(question, search, chat)

    assert search.calls == [(question, 5)]
    assert draft.trace[0].query == question
    assert f"问题：{question}\n\n" in chat.prompts[0]


@pytest.mark.parametrize("top_k", [0, -1, True, 1.5])
def test_invalid_top_k_is_rejected_before_port_calls(top_k):
    search = _FixedSearch(())
    chat = _ScriptedChat('{"verdict": "NONE"}')

    with pytest.raises(ValueError, match="top_k must be positive"):
        qa.Qa().answer("问题", search, chat, top_k=top_k)

    assert search.calls == []
    assert chat.prompts == []


def test_json_parser_recursion_failure_is_sanitized():
    reply = '{"verdict": ' + "[" * 2000 + '"NONE"' + "]" * 2000 + "}"
    chat = _ScriptedChat(reply)

    with pytest.raises(RuntimeError) as failure:
        qa.Qa().answer("问题", _FixedSearch(_evidence()), chat)

    _assert_safe_error(failure.value, "judge", "INVALID_JUDGE_OUTPUT")
    assert len(chat.prompts) == 1


def test_evidence_and_returned_trace_are_immutable():
    evidence = _evidence()
    draft = qa.Qa().answer(
        "问题", _FixedSearch(evidence), _ScriptedChat('{"verdict": "SUFFICIENT"}', "回答 [1]。"),
    )

    with pytest.raises(FrozenInstanceError):
        evidence[0].text = "changed"
    with pytest.raises(FrozenInstanceError):
        draft.answer = "changed"
    with pytest.raises(FrozenInstanceError):
        draft.trace[0].decision = "NONE"
    with pytest.raises(FrozenInstanceError):
        draft.trace[0].retrieved[0].rank = 99
    with pytest.raises(TypeError):
        draft.trace[0] = draft.trace[0]
    with pytest.raises(TypeError):
        draft.trace[0].retrieved[0] = draft.trace[0].retrieved[0]


def test_reusing_qa_does_not_accumulate_or_mutate_previous_trace():
    service = qa.Qa()
    first = service.answer(
        "第一次", _FixedSearch(_evidence()), _ScriptedChat('{"verdict": "SUFFICIENT"}', "回答 [2]。"),
    )
    original = asdict(first)

    second = service.answer("第二次", _FixedSearch(()), _ScriptedChat('{"verdict": "NONE"}'))

    assert asdict(first) == original
    assert len(second.trace) == 1
    assert second.trace[0].query == "第二次"
    assert second.trace[0].retrieved == ()
    assert second.trace[0].decision == "NONE"


def test_generation_failure_leaves_no_trace_in_next_question():
    service = qa.Qa()
    failed_chat = _ScriptedChat('{"verdict": "SUFFICIENT"}', RuntimeError(_PRIVATE_DETAILS))
    with pytest.raises(qa.QaError):
        service.answer("失败的问题", _FixedSearch(_evidence()), failed_chat)

    draft = service.answer("新问题", _FixedSearch(()), _ScriptedChat('{"verdict": "NONE"}'))

    assert len(draft.trace) == 1
    assert draft.trace[0].query == "新问题"
    assert draft.trace[0].retrieved == ()


def test_concurrent_questions_on_one_qa_keep_separate_evidence_and_traces():
    barrier = Barrier(2)

    class InterleavedChat(_ScriptedChat):
        def complete(self, prompt):
            barrier.wait(timeout=5)
            return super().complete(prompt)

    service = qa.Qa()
    first_evidence = (qa.Evidence(11, 1, "first evidence", "", 0.8),)
    second_evidence = (qa.Evidence(22, 2, "second evidence", "", 0.9),)
    first_chat = InterleavedChat('{"verdict": "SUFFICIENT"}', "first answer [1]")
    second_chat = InterleavedChat('{"verdict": "PARTIAL"}', "second answer [1]")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_call = pool.submit(service.answer, "first question", _FixedSearch(first_evidence), first_chat)
        second_call = pool.submit(service.answer, "second question", _FixedSearch(second_evidence), second_chat)
        first, second = first_call.result(timeout=10), second_call.result(timeout=10)

    for draft, question, chunk_id, verdict in (
        (first, "first question", 11, "SUFFICIENT"),
        (second, "second question", 22, "PARTIAL"),
    ):
        assert draft.chunk_ids == (chunk_id,)
        assert len(draft.trace) == 1
        assert draft.trace[0].query == question
        assert draft.trace[0].decision == verdict
        assert [(hit.rank, hit.chunk_id) for hit in draft.trace[0].retrieved] == [(1, chunk_id)]
    assert first.answer == "first answer [1]"
    assert second.answer == "second answer [1]"
    assert all("first evidence" in prompt and "second evidence" not in prompt for prompt in first_chat.prompts)
    assert all("second evidence" in prompt and "first evidence" not in prompt for prompt in second_chat.prompts)
