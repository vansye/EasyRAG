"""Executable A/B contract: exact source spans, storage limits, and whitespace gaps."""

import pytest

from app.modules.knowledge._validation import validate_chunks
from app.modules.knowledge.public import ChunkWrite, InputRejected
from app.modules.retrieval.public import split_markdown


def sparse_tokens(text):
    return len(text.split()) + 2


@pytest.mark.parametrize('body', [
    'A plain note without headings.\n',
    '# ' + 'A' * 600 + '\nBody.\n',
    '# ' + '🙂' * 600 + '\nBody.\n',
    'start\n' + ' ' * 70000 + '\nend',
    'start' + ' ' * 70000,
    ' ' * 70000 + 'end',
    '开始\n' + '\u3000' * 23000 + '\n结束',
    '猫🙂' * 10000,
], ids=['plain', 'long-heading', 'unicode-heading', 'middle-space', 'trailing-space',
        'leading-space', 'unicode-space', 'dense-utf8'])
def test_splitter_output_obeys_chunk_storage_contract(body):
    chunks = split_markdown(body, count_tokens=sparse_tokens)
    assert chunks
    source, previous_end = body.encode('utf-8'), 0
    for chunk in chunks:
        assert 0 < len(chunk.text.encode('utf-8')) <= 65535
        assert chunk.text.strip()
        assert len(chunk.heading_path) <= 512
        assert chunk.token_count == sparse_tokens(chunk.embedding_text) <= 512
        assert source[chunk.byte_start:chunk.byte_end].decode('utf-8') == chunk.text
        assert not source[previous_end:chunk.byte_start].decode('utf-8').strip()
        previous_end = chunk.byte_end
    assert not source[previous_end:].decode('utf-8').strip()
    writes = tuple(ChunkWrite(seq, c.text, c.byte_start, c.byte_end, c.heading_path, c.token_count)
                   for seq, c in enumerate(chunks))
    validate_chunks(body, writes)
    if body.startswith('# '):
        assert {chunk.heading_path for chunk in chunks} == {body.splitlines()[0][2:514]}


@pytest.mark.parametrize('size', [65535, 65536])
def test_chunk_storage_byte_limit_applies_to_splitting_and_merging(size):
    body = 'x' * size
    chunks = split_markdown(body, count_tokens=sparse_tokens)
    assert len(chunks) == (1 if size == 65535 else 2)
    assert ''.join(chunk.text for chunk in chunks) == body
    assert max(len(chunk.text.encode()) for chunk in chunks) <= 65535


def two_words(prefix, gap, suffix):
    content = prefix + '猫' + gap + '狗' + suffix
    first_start = len(prefix.encode())
    first_end = first_start + len('猫'.encode())
    second_start = first_end + len(gap.encode())
    return content, (
        ChunkWrite(0, '猫', first_start, first_end, '', 1),
        ChunkWrite(1, '狗', second_start, second_start + len('狗'.encode()), '', 1),
    )


@pytest.mark.parametrize('whitespace', [' ', '\t\r\n', '\u3000', '\u00a0', '\x85'])
def test_chunk_storage_allows_only_whitespace_gaps(whitespace):
    content, chunks = two_words(whitespace, whitespace, whitespace)
    validate_chunks(content, chunks)


@pytest.mark.parametrize('position', range(3), ids=['prefix', 'middle', 'suffix'])
@pytest.mark.parametrize('omitted', ['x', '\u200b'], ids=['visible', 'zero-width-space'])
def test_chunk_storage_rejects_any_missing_non_whitespace(position, omitted):
    gaps = [' ', ' ', ' ']
    gaps[position] = omitted
    content, chunks = two_words(*gaps)
    with pytest.raises(InputRejected):
        validate_chunks(content, chunks)


def test_chunk_storage_still_rejects_overlap_and_split_utf8():
    with pytest.raises(InputRejected):
        validate_chunks('abcd', (
            ChunkWrite(0, 'ab', 0, 2, '', 1), ChunkWrite(1, 'bcd', 1, 4, '', 1),
        ))
    with pytest.raises(InputRejected):
        validate_chunks('\u3000猫', (ChunkWrite(0, '猫', 2, 6, '', 1),))
