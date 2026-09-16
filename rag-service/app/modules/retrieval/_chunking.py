"""标题感知切片；正文片段保持原样，只跳过纯空白片段，以 UTF-8 字节定位。"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache


_MAX_CHUNK_BYTES = 65535
_MAX_HEADING_CODE_POINTS = 512

_HEADING = re.compile(r"^ {0,3}(#{1,3})(?:[ \t]+(.*?))?[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def _embedding_text(text: str, heading_path: str) -> str:
    return f"{text}\n{heading_path}" if heading_path else text


@dataclass(frozen=True)
class Chunk:
    text: str
    heading_path: str
    byte_start: int
    byte_end: int
    token_count: int

    @property
    def embedding_text(self) -> str:
        return _embedding_text(self.text, self.heading_path)


@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    headings: tuple[str, ...]

    @property
    def heading_path(self) -> str:
        return " > ".join(title for title in self.headings if title)[:_MAX_HEADING_CODE_POINTS]


def _structure(text: str) -> tuple[list[_Span], list[int], list[int]]:
    sections = []
    paragraph_ends = []
    sentence_ends = []
    heading_stack: list[tuple[int, str]] = []
    section_start = 0
    position = 0
    fence_character = ""
    fence_length = 0

    for raw_line in re.findall(r"[^\r\n]*(?:\r\n?|\n)|[^\r\n]+$", text):
        line_start = position
        position += len(raw_line)
        line = raw_line.rstrip("\r\n")
        fence = _FENCE.match(line)
        if fence_character:
            if (fence and fence[1][0] == fence_character
                    and len(fence[1]) >= fence_length and not fence[2].strip(" \t")):
                fence_character = ""
                paragraph_ends.append(position)
            continue
        if fence and not (fence[1][0] == "`" and "`" in fence[2]):
            paragraph_ends.append(line_start)
            fence_character = fence[1][0]
            fence_length = len(fence[1])
            continue

        heading = _HEADING.match(line)
        if heading is None:
            if not line.strip():
                paragraph_ends.append(position)
            sentence_ends.extend(
                line_start + ending.end()
                for ending in re.finditer(r"[。！？!?；;]|[.](?=\s|$)", line)
            )
            continue
        if text[section_start:line_start].strip():
            sections.append(_Span(section_start, line_start, tuple(entry[1] for entry in heading_stack)))
            section_start = line_start
        level = len(heading[1])
        title = re.sub(r"(?:^|[ \t]+)#+[ \t]*$", "", heading[2] or "").strip()
        while heading_stack and heading_stack[-1][0] >= level:
            heading_stack.pop()
        heading_stack.append((level, title))

    sections.append(_Span(section_start, len(text), tuple(entry[1] for entry in heading_stack)))
    return sections, paragraph_ends, sentence_ends


def _common_headings(previous: _Span, current: _Span) -> tuple[str, ...]:
    common = []
    for previous_title, current_title in zip(previous.headings, current.headings):
        if previous_title != current_title:
            break
        common.append(previous_title)
    return tuple(common)


def split_markdown(
    text: str,
    *,
    count_tokens: Callable[[str], int],
    max_tokens: int = 512,
    min_tokens: int = 64,
) -> list[Chunk]:
    """计数器须包含模型特殊 token；512 限制完整 embedding 输入而非仅正文。"""
    if max_tokens <= 0 or not 0 <= min_tokens <= max_tokens:
        raise ValueError("tokens limits require 0 <= min_tokens <= max_tokens and max_tokens > 0")
    if not text.strip():
        return []

    byte_offsets = [0]
    for character in text:
        byte_offsets.append(byte_offsets[-1] + len(character.encode("utf-8")))

    @lru_cache(maxsize=None)
    def measure(span: _Span) -> int:
        return count_tokens(_embedding_text(text[span.start:span.end], span.heading_path))

    def fits(span: _Span) -> bool:
        return (byte_offsets[span.end] - byte_offsets[span.start] <= _MAX_CHUNK_BYTES
                and measure(span) <= max_tokens)

    sections, paragraph_ends, sentence_ends = _structure(text)

    def subdivide(span: _Span) -> list[_Span]:
        if not text[span.start:span.end].strip():
            return []
        if fits(span):
            return [span]
        if span.end - span.start <= 1:
            raise ValueError("max_tokens cannot fit text plus heading_path and special tokens")
        midpoint = (span.start + span.end) // 2
        boundary = midpoint
        for endings in (paragraph_ends, sentence_ends):
            candidates = [ending for ending in endings if span.start < ending < span.end]
            if candidates:
                boundary = min(candidates, key=lambda ending: abs(ending - midpoint))
                break
        return (
            subdivide(_Span(span.start, boundary, span.headings))
            + subdivide(_Span(boundary, span.end, span.headings))
        )

    merged: list[_Span] = []
    pieces = [piece for section in sections for piece in subdivide(section)]
    for span in pieces:
        if merged and (measure(merged[-1]) < min_tokens or measure(span) < min_tokens):
            candidate = _Span(merged[-1].start, span.end, _common_headings(merged[-1], span))
            if fits(candidate):
                merged[-1] = candidate
                continue
        merged.append(span)

    return [
        Chunk(
            text=text[span.start:span.end],
            heading_path=span.heading_path,
            byte_start=byte_offsets[span.start],
            byte_end=byte_offsets[span.end],
            token_count=measure(span),
        )
        for span in merged
    ]
