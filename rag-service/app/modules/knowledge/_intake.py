"""Compatible UTF-8 intake and metadata rules, independent of persistence."""

import hashlib
import re

from ._types import InputRejected, NewDocument


MAX_CONTENT_BYTES = 1024 * 1024
# Character.isWhitespace used by Java String.strip/isBlank, excluding NBSP/NEL.
JAVA_WHITESPACE = "\t\n\v\f\r\x1c\x1d\x1e\x1f \u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2008\u2009\u200a\u2028\u2029\u205f\u3000"
FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
KEY_VALUE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*)[ \t]*:[ \t]*([^\n\r\x85\u2028\u2029]*)$")
LIST_ITEM = re.compile(r"^-[ \t]+([^\n\r\x85\u2028\u2029]*)$")
H1 = re.compile(r"(?:\A|(?<=[\n\r\x85\u2028\u2029])) {0,3}#[ \t]+([^\n\r\x85\u2028\u2029]*?)[ \t]*(?=[\n\r\x85\u2028\u2029]|\Z)")


def java_strip(value: str) -> str:
    return value.strip(JAVA_WHITESPACE)


def content_hash(content: str) -> str:
    normalized = java_strip(content.replace("\r\n", "\n").replace("\r", "\n"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def validate_content(content: str) -> str:
    if not isinstance(content, str) or not java_strip(content):
        raise InputRejected("正文不能为空")
    try:
        encoded = content.encode("utf-8", errors="strict")
    except UnicodeError as failure:
        raise InputRejected("正文不是有效的 UTF-8 文本") from failure
    if len(encoded) > MAX_CONTENT_BYTES:
        raise InputRejected(f"正文 {len(encoded):,} 字节超过收录上限 {MAX_CONTENT_BYTES:,} 字节，请拆分后再保存")
    return content


def _unquote(value: str) -> str:
    return value[1:-1] if len(value) >= 2 and value.startswith('"') and value.endswith('"') else value


def _tags(value: str) -> tuple[str, ...]:
    parts, current, quoted = [], [], False
    for character in value[1:-1]:
        if character == '"':
            quoted = not quoted
            current.append(character)
        elif character == "," and not quoted:
            parts.append("".join(current))
            current = []
        else:
            current.append(character)
    parts.append("".join(current))
    return tuple(tag for part in parts if (tag := java_strip(_unquote(java_strip(part)))))


def _block_tags(lines: list[str]) -> tuple[str, ...]:
    """YAML block sequence under `tags:`; the first line that is not an item ends it."""
    tags = []
    for line in lines:
        item = LIST_ITEM.fullmatch(java_strip(line))
        if item is None:
            break
        if tag := java_strip(_unquote(java_strip(item.group(1)))):
            tags.append(tag)
    return tuple(tags)


def metadata(content: str, fallback: str) -> tuple[str, tuple[str, ...]]:
    block = FRONTMATTER.match(content)
    title, tags, body = None, (), content
    if block:
        body = content[block.end():]
        lines = block.group(1).split("\n")
        for index, line in enumerate(lines):
            entry = KEY_VALUE.fullmatch(java_strip(line))
            if entry is None:
                continue
            key, raw = entry.groups()
            value = java_strip(raw)
            if key == "title" and title is None and value.count('"') % 2 == 0:
                title = _unquote(value)
            elif key == "tags" and value.startswith("[") and value.endswith("]"):
                tags = _tags(value)
            elif key == "tags" and not value:
                tags = _block_tags(lines[index + 1:])
    if title is not None and java_strip(title):
        return java_strip(title)[:512], tags
    heading = H1.search(body)
    if heading and java_strip(heading.group(1)):
        return java_strip(heading.group(1))[:512], tags
    return (fallback if java_strip(fallback) else "未命名文档")[:512], tags


def prepare_upload(filename: str, content_bytes: bytes) -> NewDocument:
    if not isinstance(filename, str) or not java_strip(filename):
        raise InputRejected("缺少文件名")
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    stem, dot, extension = name.rpartition(".")
    extension = extension.lower() if dot else ""
    if extension not in {"md", "txt"}:
        suffix = f" .{extension}" if java_strip(extension) else ""
        raise InputRejected(f"不支持的文件类型{suffix}（仅支持 .md / .txt）")
    if len(content_bytes) > MAX_CONTENT_BYTES:
        raise InputRejected(f"文件 {len(content_bytes):,} 字节超过收录上限 {MAX_CONTENT_BYTES:,} 字节，请拆分后再上传")
    try:
        content = content_bytes.decode("utf-8", errors="strict").removeprefix("\ufeff")
    except UnicodeError as failure:
        raise InputRejected("文件内容不是有效的 UTF-8 文本") from failure
    if not java_strip(content):
        raise InputRejected("文件内容为空")
    title, tags = metadata(content, stem)
    return NewDocument(name[:1024], title, content, content_hash(content), tags)
