"""Java intake compatibility at the owning module's public boundary."""

import hashlib

import pytest

from app.modules.knowledge import public as knowledge


def test_upload_decodes_bom_and_preserves_original_bytes_and_metadata():
    raw = '\ufeff---\r\ntitle: "🦊 Notes"\r\ntags: ["a,b", db, "中文"]\r\n---\r\n# Other\r\n正文'
    document = knowledge.prepare_upload(r'C:\fakepath\note.MD', raw.encode())
    assert document.source_uri == 'note.MD'
    assert document.title == '🦊 Notes'
    assert document.tags == ('a,b', 'db', '中文')
    assert document.content == raw[1:]


@pytest.mark.parametrize('filename, raw', [
    ('', b'text'), ('x.pdf', b'text'), ('x.md', b'\xff'),
    ('x.md', b' \r\n\t'), ('x.md', b'x' * (1024 * 1024 + 1)),
], ids=['missing-name', 'unsupported-type', 'invalid-utf8', 'blank', 'oversized'])
def test_rejects_invalid_uploads(filename, raw):
    with pytest.raises(knowledge.InputRejected):
        knowledge.prepare_upload(filename, raw)


@pytest.mark.parametrize('edge', ['\u00a0', '\u0085', '\u2007', '\u202f', '\ufeff'])
def test_hash_keeps_characters_java_strip_does_not_remove(edge):
    content = edge + 'abc' + edge
    assert knowledge.content_hash(content) == hashlib.sha256(content.encode()).hexdigest()


def test_hash_normalizes_line_endings_and_java_whitespace_only():
    assert knowledge.content_hash('\u3000\t a\r\nb\rc \u001c') == hashlib.sha256(b'a\nb\nc').hexdigest()


def test_frontmatter_unbalanced_title_falls_back_to_heading():
    document = knowledge.prepare_upload('fallback.txt', b'---\ntitle: "broken\n---\n# Correct\nBody')
    assert document.title == 'Correct'


@pytest.mark.parametrize('terminator', ['\r', '\x85', '\u2028', '\u2029'])
def test_frontmatter_ignores_values_crossing_java_line_terminators(terminator):
    raw = f'---\ntitle: One{terminator}Two\ntags: [a{terminator}b]\n---\n# Fallback\nBody'
    document = knowledge.prepare_upload('note.md', raw.encode())
    assert document.title == 'Fallback'
    assert document.tags == ()


def test_unsupported_upload_reports_extension():
    with pytest.raises(knowledge.InputRejected, match=r'\.pdf'):
        knowledge.prepare_upload('x.pdf', b'text')


@pytest.mark.parametrize('filename', ['README', 'note.', 'note.\u3000'])
def test_missing_extension_has_no_invented_suffix(filename):
    with pytest.raises(knowledge.InputRejected) as failure:
        knowledge.prepare_upload(filename, b'text')
    assert str(failure.value) == '不支持的文件类型（仅支持 .md / .txt）'


def test_title_and_filename_limits_are_unicode_code_points():
    document = knowledge.prepare_upload('🦊' * 1200 + '.md', ('# ' + '🐈' * 600).encode())
    assert document.title == '🐈' * 512
    assert len(document.source_uri) == 1024


def test_upload_of_non_breaking_space_is_not_java_blank():
    document = knowledge.prepare_upload('note.md', '\u00a0'.encode())
    assert document.content == '\u00a0'


@pytest.mark.parametrize('content', [None, '', '\t\u3000', '\ud800', '猫' * 400000],
                         ids=['null', 'empty', 'blank', 'surrogate', 'oversized'])
def test_update_validation_rejects_bad_body_before_index_is_changed(content):
    with pytest.raises(knowledge.InputRejected):
        knowledge.validate_content(content)


def test_update_validation_does_not_strip_bom_or_line_endings():
    raw = '\ufeff# 标题\r\n正文'
    assert knowledge.validate_content(raw) == raw
