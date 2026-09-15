"""切片逻辑用确定性计数器测试，真实 BGE tokenizer 由离线基线验证。"""

import pytest


def test_titles_keep_paths_and_exact_utf8_offsets():
    from app.modules.retrieval.public import split_markdown

    text = "序言🙂\r\n# 主标题\r\n简介。\r\n## 子章节\r\n内容。\r\n### 深层\r\n细节。\r\n## 另一节\r\n结束。"
    chunks = split_markdown(text, count_tokens=len, min_tokens=0)

    assert [chunk.heading_path for chunk in chunks] == [
        "", "主标题", "主标题 > 子章节", "主标题 > 子章节 > 深层", "主标题 > 另一节"
    ]
    assert "".join(chunk.text for chunk in chunks) == text
    assert chunks[0].byte_start == 0
    assert chunks[-1].byte_end == len(text.encode("utf-8"))
    for chunk in chunks:
        assert text.encode("utf-8")[chunk.byte_start:chunk.byte_end].decode("utf-8") == chunk.text
        expected = f"{chunk.text}\n{chunk.heading_path}" if chunk.heading_path else chunk.text
        assert chunk.embedding_text == expected
        assert chunk.token_count == len(expected)
    for previous, current in zip(chunks, chunks[1:]):
        assert previous.byte_end == current.byte_start


@pytest.mark.parametrize("opening,short_closer,closing", [
    ("```python", "``", "```"),
    ("~~~~python", "~~~", "~~~~~"),
    ("````python", "```", "`````"),
])
def test_fenced_code_is_not_parsed_as_headings(opening, short_closer, closing):
    from app.modules.retrieval.public import split_markdown

    text = f"# Root\n{opening}\n# code\n\n{short_closer}\n## still code\n{closing}\n## Next\nBody."
    chunks = split_markdown(text, count_tokens=len, min_tokens=0)

    assert [chunk.heading_path for chunk in chunks] == ["Root", "Root > Next"]
    assert "## still code" in chunks[0].text
    assert "".join(chunk.text for chunk in chunks) == text


def test_only_three_atx_levels_and_valid_closing_hashes_are_headings():
    from app.modules.retrieval.public import split_markdown

    text = "# Root ###\nIntro.\n## Child###\n#### Not a section\n#not-a-heading\nBody."
    chunks = split_markdown(text, count_tokens=len, min_tokens=0)

    assert [chunk.heading_path for chunk in chunks] == ["Root", "Root > Child###"]
    assert "#### Not a section" in chunks[1].text


def test_small_adjacent_sections_merge_under_their_common_heading():
    from app.modules.retrieval.public import split_markdown

    text = "# Root\nIntro.\n## A\nA.\n## B\nB.\n"
    chunks = split_markdown(text, count_tokens=len, min_tokens=64)

    assert len(chunks) == 1
    assert chunks[0].text == text
    assert chunks[0].heading_path == "Root"


@pytest.mark.parametrize("text", ["", " \t\r\n"])
def test_blank_documents_have_no_chunks(text):
    from app.modules.retrieval.public import split_markdown

    assert split_markdown(text, count_tokens=len) == []


@pytest.mark.parametrize("maximum,minimum", [(0, 0), (64, 65), (512, -1)])
def test_invalid_chunk_limits_are_rejected(maximum, minimum):
    from app.modules.retrieval.public import split_markdown

    with pytest.raises(ValueError, match="tokens"):
        split_markdown("text", count_tokens=len, max_tokens=maximum, min_tokens=minimum)


def test_long_sections_split_at_paragraphs_before_sentences():
    from app.modules.retrieval.public import split_markdown

    paragraphs = ["# Topic\n" + "甲" * 20 + "\n\n", "乙" * 20 + "\n\n", "丙" * 20]
    chunks = split_markdown("".join(paragraphs), count_tokens=len, max_tokens=45, min_tokens=0)

    assert [chunk.text for chunk in chunks] == paragraphs
    assert all(chunk.heading_path == "Topic" for chunk in chunks)
    assert all(chunk.token_count <= 45 for chunk in chunks)


def test_long_paragraphs_fall_back_to_sentences():
    from app.modules.retrieval.public import split_markdown

    sentences = ["甲" * 20 + "。", "乙" * 20 + "！", "丙" * 20 + "？"]
    chunks = split_markdown("".join(sentences), count_tokens=len, max_tokens=30, min_tokens=0)

    assert [chunk.text for chunk in chunks] == sentences


def test_hard_splits_keep_unicode_and_contiguous_byte_offsets():
    from app.modules.retrieval.public import split_markdown

    text = "🙂汉" * 90
    chunks = split_markdown(text, count_tokens=len, max_tokens=31, min_tokens=0)

    assert len(chunks) > 1
    assert "".join(chunk.text for chunk in chunks) == text
    for chunk in chunks:
        assert chunk.token_count <= 31
        assert text.encode("utf-8")[chunk.byte_start:chunk.byte_end].decode("utf-8") == chunk.text
    for previous, current in zip(chunks, chunks[1:]):
        assert previous.byte_end == current.byte_start


def test_code_block_that_fits_is_not_split_at_internal_blank_lines():
    from app.modules.retrieval.public import split_markdown

    block = "```python\n# comment\n\nvalue=1\n```\n"
    text = "# Root\n前言。\n" + block + "附言" * 12 + "。"
    chunks = split_markdown(text, count_tokens=len, max_tokens=50, min_tokens=0)

    assert len(chunks) > 1
    assert any(block in chunk.text for chunk in chunks)
    assert all(chunk.token_count <= 50 for chunk in chunks)


def test_oversized_code_block_hard_splits_without_creating_headings():
    from app.modules.retrieval.public import split_markdown

    text = "# Root\n```python\n" + "# not a section\nvalue=1\n" * 30 + "```\n"
    chunks = split_markdown(text, count_tokens=len, max_tokens=96, min_tokens=0)

    assert all(chunk.token_count <= 96 for chunk in chunks)
    assert all(chunk.heading_path == "Root" for chunk in chunks)
    assert "".join(chunk.text for chunk in chunks) == text


def test_budget_includes_heading_and_special_tokens():
    from app.modules.retrieval.public import split_markdown

    text = "# A long heading\n" + "甲" * 30
    count_tokens = lambda content: len(content) + 2
    chunks = split_markdown(text, count_tokens=count_tokens, max_tokens=32, min_tokens=0)

    assert "".join(chunk.text for chunk in chunks) == text
    assert all(chunk.token_count == count_tokens(chunk.embedding_text) for chunk in chunks)
    assert all(chunk.token_count <= 32 for chunk in chunks)


def test_heading_larger_than_budget_is_reported_not_truncated():
    from app.modules.retrieval.public import split_markdown

    with pytest.raises(ValueError, match="heading|max_tokens"):
        split_markdown("# " + "长标题" * 20 + "\n正文", count_tokens=len, max_tokens=20, min_tokens=0)


def test_minimum_is_soft_when_merging_would_exceed_maximum():
    from app.modules.retrieval.public import split_markdown

    text = "a" * 8 + "\n\n" + "b" * 25
    chunks = split_markdown(text, count_tokens=len, max_tokens=30, min_tokens=20)

    assert len(chunks) == 2
    assert min(chunk.token_count for chunk in chunks) < 20
    assert all(chunk.token_count <= 30 for chunk in chunks)
    assert "".join(chunk.text for chunk in chunks) == text


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_unicode_whitespace_does_not_close_a_code_fence(fence):
    from app.modules.retrieval.public import split_markdown

    body = "code " * 18
    text = f"{fence}\n{body}\n{fence}\u00a0\n# inside\n{body}\n{fence}\n# outside\n{body}"
    chunks = split_markdown(text, count_tokens=len)

    assert [chunk.heading_path for chunk in chunks] == ["", "outside"]
    assert "# inside" in chunks[0].text
    assert "".join(chunk.text for chunk in chunks) == text


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_unicode_separator_does_not_create_a_markdown_line(fence):
    from app.modules.retrieval.public import split_markdown

    body = "code " * 18
    text = f"{fence}\n{body}\ncode\u2028{fence}\n# inside\n{body}\n{fence}\n# outside\n{body}"
    chunks = split_markdown(text, count_tokens=len)

    assert [chunk.heading_path for chunk in chunks] == ["", "outside"]
    assert "# inside" in chunks[0].text
    assert "".join(chunk.text for chunk in chunks) == text


def test_carriage_return_only_fence_lines_are_preserved():
    from app.modules.retrieval.public import split_markdown

    text = "# Root\r```python\r# code\r```\r## Next\rbody"
    chunks = split_markdown(text, count_tokens=len, min_tokens=0)

    assert [chunk.heading_path for chunk in chunks] == ["Root", "Root > Next"]
    assert "# code" in chunks[0].text
    assert "".join(chunk.text for chunk in chunks) == text
