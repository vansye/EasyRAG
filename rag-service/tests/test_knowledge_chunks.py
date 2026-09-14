"""Chunk validation belongs to A, independent of the tokenizer and vector store."""

from dataclasses import replace

import pytest

from app.modules.knowledge.public import ChunkWrite, InputRejected
from app.modules.knowledge._validation import validate_chunks, validate_index_error


def test_byte_ranges_must_end_on_valid_utf8_boundaries():
    with pytest.raises(InputRejected):
        validate_chunks('猫a', (ChunkWrite(0, '猫', 0, 2, '', 1), ChunkWrite(1, 'a', 2, 4, '', 1)))


@pytest.mark.parametrize('changes', [
    {'seq': True}, {'byte_start': True}, {'byte_end': False}, {'token_count': True},
    {'text': None}, {'text': '\ud800'}, {'heading_path': None}, {'heading_path': '\ud800'},
    {'heading_path': '🐱' * 513}, {'byte_start': 1}, {'byte_end': 0},
], ids=['bool-seq','bool-start','bool-end','bool-tokens','null-text','surrogate-text',
        'null-heading','surrogate-heading','overlong-heading','gap','empty-range'])
def test_invalid_chunk_fields_are_rejected(changes):
    draft = replace(ChunkWrite(0, 'hello', 0, 5, '', 2), **changes)
    with pytest.raises(InputRejected):
        validate_chunks('hello', (draft,))


@pytest.mark.parametrize('blank', [' ', '\u00a0', '\x85', '\u2007', '\u202f'])
def test_chunk_blank_semantics_match_java_whitespace_spacechar_and_nel(blank):
    with pytest.raises(InputRejected):
        validate_chunks(blank, (ChunkWrite(0, blank, 0, len(blank.encode()), '', 1),))


def test_text_mysql_byte_limit_and_heading_code_point_limit():
    content = '猫' * 21845
    validate_chunks(content, (ChunkWrite(0, content, 0, 65535, '🐱'*512, 1),))
    with pytest.raises(InputRejected):
        validate_chunks(content+'a', (ChunkWrite(0, content+'a', 0, 65536, '', 1),))


def test_index_error_accepts_java_nonblank_and_unicode_codepoint_limit():
    validate_index_error('\u00a0')
    validate_index_error('🐱'*1024)
    with pytest.raises(InputRejected):
        validate_index_error('🐱'*1025)
