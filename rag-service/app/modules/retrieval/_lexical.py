"""Lexical ranking and rank fusion owned by retrieval; no vector store or business data here."""

from __future__ import annotations

import logging
import re
import warnings
from collections.abc import Iterable, Mapping, Sequence

with warnings.catch_warnings():
    # jieba 0.42.1 still compiles non-raw regex strings; Python 3.12+ warns once per fresh compile.
    warnings.simplefilter("ignore", SyntaxWarning)
    import jieba
from rank_bm25 import BM25Okapi

jieba.setLogLevel(logging.ERROR)

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+#-]*")
_IDENTIFIER_PART = re.compile(r"[a-z0-9]+")
_HAN = re.compile(r"[一-鿿]")
# Function words carry no topical signal but dominate term frequency in Chinese prose.
_STOPWORDS = frozenset(
    "的 了 是 在 和 与 或 及 等 都 也 就 不 有 这 那 个 为 以 被 把 对 从 到 向 于 之 其 而 并 但 则 所 "
    "我 你 他 她 它 我们 你们 他们 什么 怎么 怎样 如何 为什么 哪 哪些 吗 呢 吧 啊 呀 么 着 过 会 能 可以 要 "
    "还 还是 又 再 很 更 最 太 比 跟 让 使 给 用 上 下 中 里 后 前 时 一 一个 一些 这个 那个 这些 那些".split()
)
RRF_RANK_CONSTANT = 60


def _han_tokens(text: str) -> list[str]:
    return [piece for piece in jieba.cut_for_search(text) if _HAN.search(piece) and piece not in _STOPWORDS]


def tokenize(text: str) -> list[str]:
    """Search-granularity Chinese segments plus lower-cased identifiers kept whole and in parts.

    `redis-check-aof`, `sync_threshold` and `chroma.sqlite3` stay single terms (jieba would split
    them at the separators) and also contribute their parts, so a query naming either form matches.
    Punctuation, whitespace and a small set of function words are dropped.
    """
    tokens: list[str] = []
    position = 0
    for match in _IDENTIFIER.finditer(text):
        tokens.extend(_han_tokens(text[position:match.start()]))
        identifier = match.group(0).lower().rstrip("._+#-")
        parts = _IDENTIFIER_PART.findall(identifier)
        tokens.append(identifier)
        if len(parts) > 1:
            tokens.extend(parts)
        position = match.end()
    tokens.extend(_han_tokens(text[position:]))
    return tokens


class LexicalIndex:
    """BM25 over identifier → text; identifiers are opaque strings chosen by the caller."""

    def __init__(self, documents: Mapping[str, Sequence[str]]) -> None:
        self._identifiers = tuple(documents)
        self._bm25 = BM25Okapi([list(tokens) for tokens in documents.values()]) if documents else None

    @classmethod
    def from_texts(cls, documents: Mapping[str, str]) -> LexicalIndex:
        return cls({identifier: tokenize(text) for identifier, text in documents.items()})

    def __len__(self) -> int:
        return len(self._identifiers)

    def rank(self, query: str, limit: int) -> list[str]:
        """Identifiers with at least one matching term, best first; never pads with non-matches."""
        if limit <= 0:
            raise ValueError("limit must be positive")
        if self._bm25 is None:
            return []
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        scores = self._bm25.get_scores(query_tokens)
        order = sorted(range(len(self._identifiers)), key=lambda index: (-float(scores[index]), index))
        return [self._identifiers[index] for index in order[:limit] if float(scores[index]) > 0.0]


def fuse_rankings(rankings: Iterable[Sequence[str]], *, rank_constant: int = RRF_RANK_CONSTANT) -> list[str]:
    """Reciprocal rank fusion: score = Σ 1 / (k + rank); ties keep the order of first appearance.

    Scores from cosine distance and BM25 live on incomparable scales, so only ranks are combined.
    """
    if rank_constant <= 0:
        raise ValueError("rank_constant must be positive")
    fused: dict[str, float] = {}
    for ranking in rankings:
        for position, identifier in enumerate(ranking, start=1):
            fused[identifier] = fused.get(identifier, 0.0) + 1.0 / (rank_constant + position)
    return sorted(fused, key=lambda identifier: -fused[identifier])
