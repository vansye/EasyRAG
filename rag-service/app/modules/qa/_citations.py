"""Validate the numbered references that the Markdown answer presents as prose."""

import re
from collections.abc import Iterator

from markdown_it import MarkdownIt
from markdown_it.rules_inline import StateInline


# Match JavaScript whitespace in both reference syntax and number conversion.
_JS_SPACE = r"\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
_REFERENCE = re.compile(rf"\[([0-9]+(?:[{_JS_SPACE}]*(?:[,，]|[-–])[{_JS_SPACE}]*[0-9]+)*)\]")
# Match the frontend's marked 18.0.13 Lexer.rules.inline.gfm.url/_backpedal
# and Tokenizer.url trimming, rather than linkify-it's different URL grammar.
# Spell out JavaScript whitespace and \w where Python's character sets differ.
_URL = re.compile(
    r"((?:[hH][tT][tT][pP][sS]?|[fF][tT][pP])://|www\.)(?:[a-zA-Z0-9\-]+\.?)+[^"
    + _JS_SPACE
    + r"<]*|[A-Za-z0-9._+-]+(@)[a-zA-Z0-9-_]+(?:\.[a-zA-Z0-9-_]*[a-zA-Z0-9])+(?![A-Za-z0-9_-])"
)
_BACKPEDAL = re.compile(
    r"""(?:[^?!.,:;*_'"~()&]+|\([^)]*\)|&(?![a-zA-Z0-9]+;$)|[?!.,:;*_'"~)]+(?!$))+"""
)


def _marked_url(state: StateInline, silent: bool) -> bool:
    # Link-label lookahead must retain its closing bracket. Explicit links also
    # tokenize their labels with linkLevel set, so URLs inside them stay labels.
    if silent or state.linkLevel:
        return False
    match = _URL.match(state.src, state.pos, state.posMax)
    if match is None:
        return False
    value = match.group(0)
    email = match.group(2) == "@"
    if not email:
        while True:
            trimmed = _BACKPEDAL.match(value).group(0)
            if trimmed == value:
                break
            value = trimmed
    href = "mailto:" + value if email else "http://" + value if match.group(1) == "www." else value
    opening = state.push("link_open", "a", 1)
    opening.attrs = {"href": href}
    state.push("text", "", 0).content = value
    state.push("link_close", "a", -1)
    state.pos += len(value)
    return True


def _text_before_url(state: StateInline, silent: bool) -> bool:
    # Let the URL rule run before ordinary Markdown splits brackets/emphasis
    # inside a URL. Existing code, escape, HTML and explicit-link rules still
    # run at their original delimiters.
    terminator = state.md.inline.terminator_re.search(state.src, state.pos, state.posMax)
    end = terminator.start() if terminator else state.posMax
    if not silent and not state.linkLevel:
        match = _URL.search(state.src, state.pos, state.posMax)
        if match is not None:
            end = min(end, match.start())
    if end == state.pos:
        return False
    if not silent:
        state.pending += state.src[state.pos:end]
    state.pos = end
    return True


def _prose(markdown: str) -> Iterator[str]:
    # Preserve escaped punctuation as text_special so \[1] is not a citation.
    parser = MarkdownIt("commonmark").enable(["table", "strikethrough"]).disable("text_join")
    # Classification only: marked recognizes file/custom destinations as links
    # too. This parser never renders HTML or follows a destination.
    parser.validateLink = lambda _href: True
    parser.inline.ruler.before("text", "answer_url", _marked_url)
    parser.inline.ruler.at("text", _text_before_url)
    for block in parser.parse(markdown):
        if block.type != "inline":
            continue
        parts: list[str] = []
        link_depth = 0
        for token in block.children or ():
            if not link_depth and token.type in ("text", "softbreak"):
                parts.append(token.content if token.type == "text" else "\n")
                continue
            if parts:
                yield "".join(parts)
                parts.clear()
            if token.type == "link_open":
                link_depth += 1
            elif token.type == "link_close":
                link_depth -= 1
        if parts:
            yield "".join(parts)


def citations_are_valid(markdown: str, evidence_count: int) -> bool:
    found = False
    for text in _prose(markdown):
        for match in _REFERENCE.finditer(text):
            found = True
            group = re.sub(rf"[{_JS_SPACE}]", "", match.group(1))
            for part in re.split(r"[,，]", group):
                bounds = re.split(r"[-–]", part)
                if len(bounds) > 2:
                    return False
                try:
                    start, end = int(bounds[0]), int(bounds[-1])
                except ValueError:
                    return False
                if not 1 <= start <= end <= evidence_count:
                    return False
    return found
