"""子 Issue B 的离线召回验收；不接业务数据库，不调用 LLM。"""

import argparse
import hashlib
import platform
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import chromadb
from chromadb.config import Settings as ChromaSettings
import httpx
from tokenizers import Tokenizer

from app.modules.retrieval.public import Chunk, split_markdown, splitter_fingerprint


K_VALUES = (1, 3, 5, 10)
RETRIEVAL_LIMIT = max(K_VALUES)
DETAIL_TOP_K = 5
SERVICE_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SERVICE_DIR.parent
TOKENIZER_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
TOKENIZER_SHA256 = "21106b6d7dab2952c1d496fb21d5dc9db75c28ed361a05f5020bbba27810dd08"
TOKENIZER_URL = f"https://huggingface.co/BAAI/bge-m3/resolve/{TOKENIZER_REVISION}/tokenizer.json"


@dataclass(frozen=True)
class Question:
    question_id: str
    text: str
    expected_source: str | None

    @property
    def category(self) -> str:
        return self.question_id[0]


@dataclass(frozen=True)
class IndexedChunk:
    source: str
    seq: int
    chunk: Chunk

    @property
    def identifier(self) -> str:
        return f"{self.source}::{self.seq}"

    @property
    def metadata(self) -> dict:
        return {
            "source": self.source,
            "seq": self.seq,
            "heading_path": self.chunk.heading_path,
            "byte_start": self.chunk.byte_start,
            "byte_end": self.chunk.byte_end,
            "token_count": self.chunk.token_count,
        }


def parse_questions(text: str) -> list[Question]:
    questions = []
    seen = set()
    for line in text.splitlines():
        if line.startswith("## 四、"):
            break
        if not line.lstrip().startswith("|"):
            continue
        columns = [column.strip() for column in line.strip().strip("|").split("|")]
        if not re.fullmatch(r"[QNPR][1-9]\d*", columns[0]):
            continue
        question_id = columns[0]
        if question_id in seen:
            raise ValueError(f"duplicate question: {question_id}")
        if len(columns) < 3 or not columns[1] or not columns[2]:
            raise ValueError(f"invalid question row: {question_id}")
        seen.add(question_id)
        source = columns[2].strip("`") if question_id[0] in "QR" else None
        questions.append(Question(question_id, columns[1], source))
    return questions


def first_hit_rank(question: Question, hits: list[dict]) -> int | None:
    if question.expected_source is None:
        return None
    return next((rank for rank, hit in enumerate(hits, start=1)
                 if hit["source"] == question.expected_source), None)


def summarize(questions: list[Question], rankings: dict[str, list[dict]]) -> dict:
    metrics = {}
    for category in ("Q", "R", "overall"):
        selected = [question for question in questions
                    if question.category in ("QR" if category == "overall" else category)]
        ranks = [first_hit_rank(question, rankings[question.question_id]) for question in selected]
        metrics[category] = {
            "total": len(selected),
            "hits_at_k": {k: sum(rank is not None and rank <= k for rank in ranks)
                          for k in K_VALUES},
            "hit_rate_at_k": {k: sum(rank is not None and rank <= k for rank in ranks) / len(selected)
                              if selected else None for k in K_VALUES},
        }
    metrics["excluded"] = {category: sum(question.category == category for question in questions)
                           for category in "NP"}
    return metrics


def load_tokenizer(path: Path) -> Tokenizer:
    tokenizer = Tokenizer.from_file(str(path))
    tokenizer.no_truncation()
    tokenizer.no_padding()
    return tokenizer


def prepare_corpus(
    corpus: Path,
    *,
    count_tokens: Callable[[str], int],
    max_tokens: int,
    min_tokens: int,
) -> tuple[list[IndexedChunk], list[dict]]:
    records = []
    manifest = []
    for path in sorted(corpus.rglob("*.md")):
        original = path.read_bytes()
        source = path.relative_to(corpus).as_posix()
        chunks = split_markdown(original.decode("utf-8"), count_tokens=count_tokens,
                                max_tokens=max_tokens, min_tokens=min_tokens)
        records.extend(IndexedChunk(source, seq, chunk) for seq, chunk in enumerate(chunks, start=1))
        manifest.append({"source": source, "sha256": hashlib.sha256(original).hexdigest(),
                         "bytes": len(original), "chunks": len(chunks)})
    return records, manifest


def embed_texts(
    client: httpx.Client,
    texts: list[str],
    *,
    model: str,
    dimensions: int,
    batch_size: int,
    progress: bool = False,
) -> list[list[float]]:
    if batch_size <= 0 or dimensions <= 0:
        raise ValueError("batch_size and dimensions must be positive")
    vectors = []
    for offset in range(0, len(texts), batch_size):
        batch = texts[offset:offset + batch_size]
        response = client.post("/api/embed", json={"model": model, "input": batch, "truncate": False})
        response.raise_for_status()
        embeddings = response.json()["embeddings"]
        if len(embeddings) != len(batch):
            raise ValueError(f"embedding count mismatch: expected {len(batch)}, got {len(embeddings)}")
        if any(len(vector) != dimensions for vector in embeddings):
            raise ValueError(f"embedding dimension mismatch: expected {dimensions}")
        vectors.extend(embeddings)
        if progress:
            print(f"  embedded {len(vectors)}/{len(texts)}", file=sys.stderr, flush=True)
    return vectors


def retrieve_chunks(
    records: list[IndexedChunk],
    document_vectors: list[list[float]],
    questions: list[Question],
    query_vectors: list[list[float]],
) -> dict[str, list[dict]]:
    client = chromadb.EphemeralClient(settings=ChromaSettings(anonymized_telemetry=False))
    collection = client.create_collection(
        name=f"baseline-{uuid4().hex}", embedding_function=None,
        metadata={"hnsw:space": "cosine"},
    )
    try:
        collection.add(
            ids=[record.identifier for record in records], embeddings=document_vectors,
            documents=[record.chunk.text for record in records],
            metadatas=[record.metadata for record in records],
        )
        rankings = {}
        for question, vector in zip(questions, query_vectors, strict=True):
            result = collection.query(query_embeddings=[vector], n_results=min(RETRIEVAL_LIMIT, len(records)),
                                      include=["metadatas", "distances"])
            rankings[question.question_id] = [
                {**metadata, "distance": float(distance)}
                for metadata, distance in zip(result["metadatas"][0], result["distances"][0], strict=True)
            ]
        return rankings
    finally:
        client.delete_collection(collection.name)


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _argument_path(path: Path) -> str:
    absolute = path.resolve()
    if absolute.is_relative_to(SERVICE_DIR):
        return absolute.relative_to(SERVICE_DIR).as_posix()
    if absolute.is_relative_to(PROJECT_ROOT):
        return "../" + absolute.relative_to(PROJECT_ROOT).as_posix()
    return absolute.as_posix()


def _quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def render_report(report: dict) -> str:
    parameters = report["parameters"]
    statistics = report["chunk_statistics"]
    lines = [
        "# 离线召回验收报告（子 Issue B）", "",
        f"> 运行时间：{report['generated_at']}。由离线 CLI 生成，未调用 LLM。", "",
        "## 验收边界", "",
        "本报告只验证切片与向量索引的离线召回；资料收录、更新同步、业务问答与完整评估平台需各自的验收证据。",
        "不实现 Agent、改写、BM25、重排、拒答或部分覆盖判断；不读取 MySQL，不打开业务持久化 Chroma。", "",
        "## 召回结果", "",
        "| 类别 | " + " | ".join(f"hit@{k}" for k in K_VALUES) + " |",
        "|---|" + "---:|" * len(K_VALUES),
    ]
    for category, label in (("Q", "Q"), ("R", "R"), ("overall", "Q+R")):
        metric = report["metrics"][category]
        cells = [f"{metric['hits_at_k'][k]}/{metric['total']}（{metric['hit_rate_at_k'][k]:.2%}）"
                 if metric["hit_rate_at_k"][k] is not None else "未评分"
                 for k in K_VALUES]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    excluded = report["metrics"]["excluded"]
    failures = [question.question_id for question in report["questions"]
                if question.category in "QR"
                and first_hit_rank(question, report["rankings"][question.question_id]) is None]
    late_hits = [f"{question.question_id}@{rank}"
                 for question in report["questions"]
                 if question.category in "QR"
                 and (rank := first_hit_rank(question, report["rankings"][question.question_id]))
                 and rank > DETAIL_TOP_K]
    lines += [
        "", f"漏召回题（前 {RETRIEVAL_LIMIT} 均未命中）：{', '.join(failures) if failures else '无'}。",
        f"命中但未进前 {DETAIL_TOP_K}：{', '.join(late_hits) if late_hits else '无'}。",
        f"N 类 {excluded['N']} 题、P 类 {excluded['P']} 题只展示召回，不进入上述分母，也不据此判定拒答或部分覆盖能力。",
        "", "**计分口径**：Q/R 均使用原问题；hit@k = 前 k 个 chunk 中任意一个来自唯一标注文档，"
        f"k ∈ {{{', '.join(str(k) for k in K_VALUES)}}}。",
        "不先按文档去重，不把同主题次要文档算正确；各 k 独立计分，排名超过该 k 不计入。",
        "命中文档不等于片段足以完整回答；本次不评估答案质量或引用忠实度。", "",
        "## 参数与数据", "",
        f"- 模型：`{parameters['model_tag']}`；请求名：`{parameters['model']}`；维度：{parameters['dimensions']}。",
        f"- Ollama 模型 digest：`{parameters['model_digest']}`。",
        f"- 端点：`{parameters['ollama_url']}`；batch_size={parameters['batch_size']}；超时={parameters['timeout_seconds']} 秒；不重试。",
        f"- tokenizer 文件：`{parameters['tokenizer_file']}`；SHA-256：`{parameters['tokenizer_sha256']}`。",
        f"- tokenizer 来源：{parameters['tokenizer_source']}。",
        "- tokenizer 禁用 truncation/padding；Ollama `/api/embed` 明确传 `truncate=false`。",
        f"- 切片：前三层 ATX 标题；max_tokens={parameters['max_tokens']}；min_tokens={parameters['min_tokens']}；无重叠。",
        "- token 计数包含特殊 token 与实际 embedding 输入：原文正文 + 换行 + 标题路径（无标题时只用正文）。",
        "- 超长节按段落、句子、Unicode 码点硬切逐级回退；栅栏内部不识别标题、段落或句子边界，超长代码块才硬切。",
        "- 短片段在不超上限时与相邻片段合并，跨节保留共同标题路径；原文中的标题仍保留。下限是合并目标，不是硬约束。",
        "- `byte_start/byte_end` 为原文 UTF-8 字节偏移（左闭右开）；正文不规范化换行。HTTP 定位契约由独立的资料管理与接口测试验证。",
        "- 索引：独立 Chroma 内存 collection，cosine 距离，检索后只删除本次 collection；无阈值过滤。",
        f"- 语料：{len(report['manifest'])} 篇、{sum(document['bytes'] for document in report['manifest'])} 字节；{statistics['count']} 个 chunk。",
        f"- 实际 chunk token 范围：{statistics['minimum']}–{statistics['maximum']}；低于软下限：{statistics['below_minimum']} 个。",
        f"- 文档 embedding 输入合计 {statistics['document_tokens']} token；全部查询合计 {statistics['query_tokens']} token。",
        f"- 语料指纹（按排序后的路径与原文 SHA-256 汇总）：`{report['corpus_sha256']}`。",
        f"- 黄金集原文件 SHA-256：`{report['golden_sha256']}`。", "",
        "## 耗时", "", "| 阶段 | 秒 |", "|---|---:|",
    ]
    for stage, duration in report["timings"].items():
        lines.append(f"| {stage} | {duration:.3f} |")
    lines += [
        "", "耗时仅代表本机本次运行；未控制模型冷启动，查询 embedding 为批量处理，不能当作单请求延迟或 QPS。", "",
        "## 复跑", "",
        "在仓库的 `rag-service/` 目录执行；先安装 `requirements.txt`，确保本地 Ollama 已拉取所选模型。",
        "每次完整重建临时索引，不复用旧向量。报告将写到显式指定的 `--output`。", "",
        "```powershell", "$env:PYTHONIOENCODING = 'utf-8'",
    ]
    if parameters["tokenizer_sha256"] == TOKENIZER_SHA256:
        lines += [
            f"New-Item -ItemType Directory -Force {_quote(parameters['tokenizer_directory'])} | Out-Null",
            f"Invoke-WebRequest -UseBasicParsing -Uri '{TOKENIZER_URL}' -OutFile {_quote(parameters['tokenizer_file'])}",
        ]
    else:
        lines.append("Write-Output 'Supply the tokenizer file with the SHA-256 recorded above before running.'")
    lines += [report["command"], "```", "",
              f"## 逐题明细（检索深度 {RETRIEVAL_LIMIT}，展示前 {DETAIL_TOP_K}）", "",
              "相似度 = 1 − cosine distance，仅作排序诊断，不代表置信度。位置以原始文件的 UTF-8 字节为单位。", ""]
    for question in report["questions"]:
        hits = report["rankings"][question.question_id]
        rank = first_hit_rank(question, hits)
        outcome = "未评分" if question.category in "NP" else (f"HIT@{rank}" if rank else "MISS")
        lines += [f"### {question.question_id} · {outcome}", "", question.text, "",
                  f"标注出处：`{question.expected_source}`" if question.expected_source else "不参与召回命中率计分。", "",
                  "| 排名 | 文档 | seq | 标题路径 | byte_start:byte_end | token | 相似度 |",
                  "|---:|---|---:|---|---|---:|---:|"]
        for position, hit in enumerate(hits[:DETAIL_TOP_K], start=1):
            lines.append(
                f"| {position} | {_cell(hit['source'])} | {hit['seq']} | {_cell(hit['heading_path'])} | "
                f"{hit['byte_start']}:{hit['byte_end']} | {hit['token_count']} | {1 - hit['distance']:.6f} |"
            )
        lines.append("")
    lines += ["## 语料指纹", "", "| 相对路径 | 字节数 | chunks | SHA-256 |", "|---|---:|---:|---|"]
    for document in report["manifest"]:
        lines.append(f"| {_cell(document['source'])} | {document['bytes']} | {document['chunks']} | `{document['sha256']}` |")
    lines += ["", "## 环境与代码指纹", ""]
    lines.extend(f"- {package}: `{installed_version}`" for package, installed_version in report["versions"].items())
    lines.append("")
    lines.extend(f"- `{source}` SHA-256: `{digest}`" for source, digest in report["code_sha256"].items())
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="子 Issue B：本地 Ollama + 隔离 Chroma 单轮召回验收")
    parser.add_argument("--corpus", type=Path, default=PROJECT_ROOT / "sample-knowledge")
    parser.add_argument("--golden-set", type=Path, default=PROJECT_ROOT / "docs/eval/golden-set-v1.md")
    parser.add_argument("--tokenizer", type=Path, default=SERVICE_DIR / "data/tokenizers" / f"bge-m3-{TOKENIZER_REVISION}.json")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="bge-m3")
    parser.add_argument("--dimensions", type=int, default=1024)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--min-tokens", type=int, default=64)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--timeout-seconds", type=float, default=180)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    if arguments.batch_size <= 0 or arguments.dimensions <= 0 or arguments.timeout_seconds <= 0:
        parser.error("batch-size, dimensions and timeout-seconds must be positive")
    if not arguments.tokenizer.is_file():
        parser.error(f"tokenizer file missing; default tokenizer download: {TOKENIZER_URL}")

    started = perf_counter()
    tokenizer = load_tokenizer(arguments.tokenizer)
    count_tokens = lambda text: len(tokenizer.encode(text, add_special_tokens=True).ids)
    golden_bytes = arguments.golden_set.read_bytes()
    questions = parse_questions(golden_bytes.decode("utf-8"))
    records, manifest = prepare_corpus(arguments.corpus, count_tokens=count_tokens,
                                       max_tokens=arguments.max_tokens, min_tokens=arguments.min_tokens)
    if not questions or not records:
        parser.error("evaluation requires questions and nonempty Markdown chunks")
    sources = {record.source for record in records}
    missing = [question.question_id for question in questions
               if question.expected_source is not None and question.expected_source not in sources]
    if missing:
        parser.error(f"expected sources missing from corpus: {', '.join(missing)}")
    timings = {"切片与准备": perf_counter() - started}

    with httpx.Client(base_url=arguments.ollama_url, timeout=arguments.timeout_seconds) as client:
        response = client.get("/api/tags")
        response.raise_for_status()
        names = {arguments.model, f"{arguments.model}:latest"}
        model = next((item for item in response.json()["models"] if item["name"] in names), None)
        if model is None:
            parser.error(f"Ollama model not installed: {arguments.model}")
        response = client.get("/api/version")
        response.raise_for_status()
        ollama_version = response.json()["version"]
        print(f"Embedding {len(records)} chunks ({len(manifest)} documents)", file=sys.stderr, flush=True)
        stage_started = perf_counter()
        document_vectors = embed_texts(
            client, [record.chunk.embedding_text for record in records], model=arguments.model,
            dimensions=arguments.dimensions, batch_size=arguments.batch_size, progress=True,
        )
        timings["文档 embedding"] = perf_counter() - stage_started
        print(f"Embedding {len(questions)} original questions", file=sys.stderr, flush=True)
        stage_started = perf_counter()
        query_vectors = embed_texts(
            client, [question.text for question in questions], model=arguments.model,
            dimensions=arguments.dimensions, batch_size=arguments.batch_size, progress=True,
        )
        timings["问题 embedding"] = perf_counter() - stage_started
    stage_started = perf_counter()
    rankings = retrieve_chunks(records, document_vectors, questions, query_vectors)
    timings["临时索引与检索"] = perf_counter() - stage_started
    timings["总计（不含 Python 导入和报告写入）"] = perf_counter() - started
    metrics = summarize(questions, rankings)
    tokenizer_digest = hashlib.sha256(arguments.tokenizer.read_bytes()).hexdigest()
    parameters = {name: value for name, value in vars(arguments).items() if not isinstance(value, Path)}
    parameters.update({
        "model_tag": model["name"], "model_digest": model["digest"],
        "tokenizer_file": _argument_path(arguments.tokenizer),
        "tokenizer_directory": _argument_path(arguments.tokenizer.parent),
        "tokenizer_sha256": tokenizer_digest,
        "tokenizer_source": TOKENIZER_URL if tokenizer_digest == TOKENIZER_SHA256 else "用户提供的本地文件，来源版本未声明",
    })
    command = [".\\.venv\\Scripts\\python.exe -m scripts.eval_retrieval"]
    command.extend(f"--{name.replace('_', '-')} {_quote(_argument_path(value) if isinstance(value, Path) else value)}"
                   for name, value in vars(arguments).items() if value is not None)
    report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "parameters": parameters, "manifest": manifest, "questions": questions,
        "rankings": rankings, "metrics": metrics, "timings": timings,
        "golden_sha256": hashlib.sha256(golden_bytes).hexdigest(),
        "corpus_sha256": hashlib.sha256("".join(f"{item['source']}\0{item['sha256']}\n" for item in manifest).encode("utf-8")).hexdigest(),
        "chunk_statistics": {
            "count": len(records), "minimum": min(record.chunk.token_count for record in records),
            "maximum": max(record.chunk.token_count for record in records),
            "below_minimum": sum(record.chunk.token_count < arguments.min_tokens for record in records),
            "document_tokens": sum(record.chunk.token_count for record in records),
            "query_tokens": sum(count_tokens(question.text) for question in questions),
        },
        "versions": {"Python": platform.python_version(), "Ollama": ollama_version,
                     **{package: version(package) for package in ("chromadb", "tokenizers", "httpx")}},
        "code_sha256": {
            "retrieval.split_markdown": splitter_fingerprint(),
            **{source: hashlib.sha256((SERVICE_DIR / source).read_bytes()).hexdigest()
               for source in ("scripts/eval_retrieval.py", "requirements.txt")},
        },
        "command": " `\n  ".join(command),
    }
    rendered = render_report(report)
    if arguments.output:
        arguments.output.write_text(rendered, encoding="utf-8", newline="\n")
        print(f"Report: {arguments.output}")
    else:
        print(rendered)
    for label, key in (("Q", "Q"), ("R", "R"), ("Q+R", "overall")):
        metric = metrics[key]
        print(label + ": " + ", ".join(f"hit@{k}={metric['hits_at_k'][k]}/{metric['total']}"
                                       for k in K_VALUES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
