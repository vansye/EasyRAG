"""把本机问答历史里的拒答与部分回答导出为难例标注表；只读，不写库，不导出答案正文。

输出两份文件：JSON（问题、状态、候选文档/名次/分数、判定）与 Markdown 标注表（留出"期望出处"列
由人工填写）。默认写到 rag-service/data/ 下，该目录被 .gitignore 排除；真实问法属于个人数据，不入库。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from app.modules.knowledge.public import DocumentNotFound, Knowledge

SERVICE_DIR = Path(__file__).resolve().parents[1]
FAILURE_STATUSES = ("REFUSED", "PARTIAL")
PAGE_SIZE = 100


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def collect_failures(knowledge: Knowledge, statuses: tuple[str, ...] = FAILURE_STATUSES) -> list[dict]:
    """Walk the history newest-first and keep one entry per failed answer, with candidate titles resolved."""
    titles: dict[int, str | None] = {}

    def title(document_id: int) -> str | None:
        if document_id not in titles:
            try:
                titles[document_id] = knowledge.get(document_id).title
            except DocumentNotFound:
                titles[document_id] = None
        return titles[document_id]

    entries = []
    page = 0
    while True:
        listing = knowledge.list_history(page=page, size=PAGE_SIZE)
        for summary in listing.items:
            if summary.status not in statuses:
                continue
            record = knowledge.get_history(summary.id)
            last_round = record.trace[-1] if record.trace else {}
            entries.append({
                "history_id": record.id,
                "created_at": record.created_at.isoformat(timespec="seconds"),
                "status": record.status,
                "model": record.model,
                "question": record.question,
                "decision": last_round.get("decision"),
                "relevant": list(last_round.get("relevant", ())),
                "candidates": [
                    {"rank": hit["rank"], "document_id": hit["document_id"], "chunk_id": hit["chunk_id"],
                     "score": round(float(hit["score"]), 4), "title": title(int(hit["document_id"]))}
                    for hit in last_round.get("retrieved", ())
                ],
            })
        if (page + 1) * PAGE_SIZE >= listing.total:
            return entries
        page += 1


def render_sheet(entries: list[dict], generated_at: str) -> str:
    lines = [
        "# 真实失败问法标注表", "",
        f"> 导出时间：{generated_at}。来源：本机问答历史里状态为 {' / '.join(FAILURE_STATUSES)} 的记录，共 {len(entries)} 条。"
        "本表与同名 JSON 只在本机使用，不入库；填好“期望出处”后再决定哪些进入难例集 v3。", "",
        "填写规则：期望出处写资料标题（唯一）；库中确实没有答案写 `无`；问法本身有歧义或属于测试残留写 `弃`。",
        "候选列出当时前 5 名（名次·标题·相似度），判定列为当时判定器结论；PARTIAL 记录同时给出当时标为相关的名次。", "",
        "| 历史 ID | 时间 | 状态 | 问题 | 候选（名次·标题·相似度） | 判定 / 相关 | 期望出处 |",
        "|---:|---|---|---|---|---|---|",
    ]
    for entry in entries:
        candidates = "；".join(
            f"{hit['rank']}·{_cell(hit['title'] or '（资料已删除）')}·{hit['score']:.3f}" for hit in entry["candidates"]
        ) or "（无候选）"
        relevant = f" / {entry['relevant']}" if entry["relevant"] else ""
        lines.append(
            f"| {entry['history_id']} | {entry['created_at'][:16].replace('T', ' ')} | {entry['status']} | "
            f"{_cell(entry['question'])} | {candidates} | {entry['decision'] or '—'}{relevant} |  |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出本机问答历史中的拒答与部分回答，作为难例标注材料（只读）")
    parser.add_argument("--status", nargs="+", default=list(FAILURE_STATUSES), choices=("REFUSED", "PARTIAL", "ANSWERED"))
    parser.add_argument("--output", type=Path, default=SERVICE_DIR / "data" / f"history-failures-{datetime.now():%Y%m%d}.json")
    arguments = parser.parse_args(argv)
    if not arguments.output.resolve().is_relative_to(SERVICE_DIR / "data"):
        parser.error("output must stay under rag-service/data (ignored by git); real questions are personal data")

    knowledge = Knowledge()
    try:
        entries = collect_failures(knowledge, tuple(arguments.status))
    finally:
        knowledge.close()
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps({"generated_at": generated_at, "statuses": arguments.status, "entries": entries},
                   ensure_ascii=False, indent=1), encoding="utf-8", newline="\n",
    )
    sheet = arguments.output.with_suffix(".md")
    sheet.write_text(render_sheet(entries, generated_at), encoding="utf-8", newline="\n")
    counts = {status: sum(entry["status"] == status for entry in entries) for status in arguments.status}
    print(f"exported {len(entries)} entries {counts} -> {arguments.output.name}, {sheet.name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
