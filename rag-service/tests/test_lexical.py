"""词法通道的纯函数契约：分词、BM25 排名与 RRF 融合，不碰 Chroma。"""

import pytest

from app.modules.retrieval._lexical import LexicalIndex, fuse_rankings, tokenize


def test_tokenize_keeps_chinese_segments_and_lower_cased_identifiers():
    tokens = tokenize("应用程序可通过 `fsync` 或 `fdatasync` 强制立即落盘；Redis 的 appendfsync=everysec、BGREWRITEAOF。")
    assert {"应用程序", "强制", "立即", "落盘"} <= set(tokens)
    assert {"fsync", "fdatasync", "appendfsync", "everysec", "bgrewriteaof", "redis"} <= set(tokens)
    assert not any(token.strip() == "" or token in "`、。；=（）" for token in tokens)


def test_tokenize_keeps_dotted_and_hyphenated_identifiers_whole_and_in_parts():
    tokens = tokenize("hnsw:sync_threshold 与 redis-check-aof --fix，chroma.sqlite3 到 v1.5.9。")
    assert {"hnsw", "sync_threshold", "sync", "threshold", "redis-check-aof", "redis", "check", "aof", "fix",
            "chroma.sqlite3", "chroma", "sqlite3", "v1.5.9"} <= set(tokens)
    assert "--fix" not in tokens and "与" not in tokens and "到" not in tokens
    assert tokenize("fsync") == ["fsync"]


def test_tokenize_drops_function_words_but_keeps_single_character_content_words():
    tokens = tokenize("为什么文件写完并返回成功，断电后数据还是可能丢？")
    assert "为什么" not in tokens and "还是" not in tokens and "后" not in tokens
    assert {"文件", "写", "返回", "成功", "断电", "数据", "丢"} <= set(tokens)


def test_lexical_rank_returns_only_matching_documents_best_first():
    index = LexicalIndex.from_texts({
        "os": "数据写入缓冲区即返回成功，断电易丢数据；fsync 强制立即落盘。",
        "rdb": "RDB 快照存在分钟级数据丢失窗口，恢复快。",
        "aof": "AOF 使用 everysec 刷盘策略最多丢失 1 秒数据。",
        "pinia": "Pinia 管理组件之间共享的状态。",
    })
    assert len(index) == 4
    ranking = index.rank("断电后数据还是可能丢？怎么强制立即落盘？", 10)
    assert ranking[0] == "os"
    assert "pinia" not in ranking
    assert index.rank("fsync", 10) == ["os"]
    assert index.rank("完全无关的问题", 10) == []
    assert index.rank("数据", 1) == [index.rank("数据", 10)[0]]


def test_lexical_rank_handles_empty_index_and_blank_query():
    assert LexicalIndex.from_texts({}).rank("数据", 5) == []
    assert LexicalIndex.from_texts({"a": "数据"}).rank("，。！", 5) == []
    with pytest.raises(ValueError):
        LexicalIndex.from_texts({"a": "数据"}).rank("数据", 0)


def test_fuse_rankings_rewards_agreement_and_keeps_first_appearance_on_ties():
    fused = fuse_rankings([["a", "b", "c"], ["b", "d"]])
    assert fused[0] == "b"
    assert fused.index("a") < fused.index("c")
    assert fused.index("d") < fused.index("c")
    assert fuse_rankings([["x", "y"], []]) == ["x", "y"]
    assert fuse_rankings([[], []]) == []
    assert fuse_rankings([["a"], ["b"]]) == ["a", "b"]
    with pytest.raises(ValueError):
        fuse_rankings([["a"]], rank_constant=0)


def test_fuse_rankings_lets_a_lexical_only_hit_outrank_low_dense_ranks():
    dense = [f"d{index}" for index in range(1, 21)]
    lexical = ["target", "d7", "d3"]
    fused = fuse_rankings([dense, lexical])
    assert fused[:2] == ["d3", "d7"]
    assert fused.index("target") < fused.index("d5")
