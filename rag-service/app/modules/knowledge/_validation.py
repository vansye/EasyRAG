"""Validate database-owned chunk invariants before any rows can be changed."""

from ._intake import java_strip
from ._types import ChunkWrite, InputRejected


def _utf8(value, field):
    if not isinstance(value, str):
        raise InputRejected(f'{field} must be a string')
    try:
        return value.encode('utf-8', errors='strict')
    except UnicodeEncodeError:
        raise InputRejected(f'{field} must be valid UTF-8') from None


def validate_chunks(content: str, chunks: tuple[ChunkWrite, ...]):
    source = _utf8(content, 'content')
    if not chunks:
        raise InputRejected('chunks must not be empty')
    previous_end = 0
    for seq, chunk in enumerate(chunks):
        if not isinstance(chunk, ChunkWrite):
            raise InputRejected('chunks must contain ChunkWrite records')
        if type(chunk.seq) is not int or chunk.seq != seq:
            raise InputRejected('seq must be consecutive from zero')
        if (type(chunk.byte_start) is not int or type(chunk.byte_end) is not int
                or not previous_end <= chunk.byte_start < chunk.byte_end <= len(source)):
            raise InputRejected('chunks must contain ordered non-overlapping UTF-8 byte ranges')
        encoded = _utf8(chunk.text, 'text')
        if not chunk.text or chunk.text.isspace():
            raise InputRejected('chunk text must not be blank')
        if len(encoded) > 65535:
            raise InputRejected('chunk text exceeds the MySQL TEXT limit')
        _utf8(chunk.heading_path, 'heading_path')
        if len(chunk.heading_path) > 512:
            raise InputRejected('heading_path exceeds 512 code points')
        if type(chunk.token_count) is not int or chunk.token_count < 0:
            raise InputRejected('token_count must be a non-negative integer')
        try:
            gap = source[previous_end:chunk.byte_start].decode('utf-8', errors='strict')
            original = source[chunk.byte_start:chunk.byte_end].decode('utf-8', errors='strict')
        except UnicodeDecodeError:
            raise InputRejected('chunk range splits a UTF-8 encoding sequence') from None
        if gap.strip():
            raise InputRejected('chunks must cover every non-whitespace character')
        if original != chunk.text:
            raise InputRejected('chunk text does not match the original content')
        previous_end = chunk.byte_end
    if source[previous_end:].decode('utf-8').strip():
        raise InputRejected('chunks must cover every non-whitespace character')


def validate_index_error(error: str):
    _utf8(error, 'index_error')
    if not java_strip(error) or len(error) > 1024:
        raise InputRejected('index_error must be non-blank and at most 1024 Unicode code points')
