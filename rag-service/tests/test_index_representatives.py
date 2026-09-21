"""同一资料内 embedding 输入完全相同的片段只索引一个向量；MySQL 仍保留全部片段行。"""

import pytest

from app.modules.retrieval import public as retrieval


def _chunk(chunk_id, text, heading_path="", tags=()):
    return retrieval.IndexChunk(chunk_id, text, heading_path, tags)


@pytest.mark.parametrize("body,expected", [
    ("a" * 2**20, 1),
    ("# T\n" + "a" * (2**20 - 4), 2),
], ids=["identical", "heading-line-in-first-chunk"])
def test_one_megabyte_of_repeated_content_yields_at_most_two_representatives(body, expected):
    drafts = retrieval.split_markdown(body, count_tokens=lambda value: len(value) // 4, max_tokens=512, min_tokens=64)
    chunks = tuple(_chunk(index + 1, draft.text, draft.heading_path) for index, draft in enumerate(drafts))
    assert len(chunks) >= 512

    representatives = retrieval.index_representatives(chunks)

    assert representatives == chunks[:expected]


def test_first_occurrence_is_kept_and_order_is_preserved():
    chunks = (
        _chunk(10, "重复段落", "A"),
        _chunk(11, "独有段落", "A"),
        _chunk(12, "重复段落", "A"),
        _chunk(13, "另一独有段落", "B"),
        _chunk(14, "重复段落", "A"),
    )

    representatives = retrieval.index_representatives(chunks)

    assert [chunk.chunk_id for chunk in representatives] == [10, 11, 13]


def test_same_body_under_different_headings_is_a_different_embedding_input():
    chunks = (_chunk(1, "正文", "第一章"), _chunk(2, "正文", "第二章"), _chunk(3, "正文", ""))

    assert retrieval.index_representatives(chunks) == chunks


def test_documents_are_independent_and_input_is_not_mutated():
    document_a = (_chunk(1, "共享段落", "H"),)
    document_b = [_chunk(2, "共享段落", "H")]

    assert retrieval.index_representatives(document_a) == document_a
    assert retrieval.index_representatives(document_b) == (document_b[0],)
    assert document_b == [_chunk(2, "共享段落", "H")]


def test_empty_input_and_non_chunk_items_are_rejected():
    assert retrieval.index_representatives(()) == ()
    with pytest.raises(ValueError):
        retrieval.index_representatives(("not a chunk",))
