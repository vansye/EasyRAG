"""模块 C 的问答管线：检索 → 三态判定 → 分流（生成 / 带边界生成 / 拒答）。

有界循环（C-1）：固定步骤、自己编排，不用 create_agent——LangChain 只用于
LLM 调用本身（B-8）。每一步确定性可测，trace 因此能结构化（U7 前端要展示
检索过程；模块 D 的评估也消费同一结构）。

边界（子 Issue C §六）：本模块无出站调用（除 LLM API）；检索只读本地
派生索引；拒答是一等行为——判定为 NONE 不进入生成。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from app.config import LlmSettings
from app.llm import create_chat_model
from app.retrieval import RetrievedChunk, retrieve
from app.index_store import IndexStore
from app.config import Settings

JudgeVerdict = Literal["SUFFICIENT", "PARTIAL", "NONE"]
AnswerStatus = Literal["ANSWERED", "PARTIAL", "REFUSED"]

_REFUSAL_ANSWER = "知识库中没有找到能回答这个问题的内容。"

_MAX_QUESTION_CODE_POINTS = 2000
# C-3：max_rounds = 2（初始检索 + 至多 1 次改写重查）。改写未启用前只有
# 初始一轮；启用后循环以此为上限，超出即按最终判定输出。
_MAX_ROUNDS = 2
_VERDICT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)


class QaError(RuntimeError):
    """问答管线的技术故障（区别于业务上的拒答）。

    判定输出无法解析属于这里——拒答是有依据的业务判断（"库里没有"），
    解析失败是"没判出来"，把后者伪装成前者会污染拒答正确率这个评估指标。
    HTTP 层映射为 503 LLM_UNAVAILABLE。
    """


class QaRequest(BaseModel):
    question: str = Field(min_length=1, max_length=_MAX_QUESTION_CODE_POINTS)


class QaTraceEntry(BaseModel):
    round_index: int
    query: str
    retrieved: list[dict[str, Any]] = Field(default_factory=list)
    decision: str


class QaResponse(BaseModel):
    answer: str
    status: AnswerStatus
    chunk_ids: list[int]
    trace: list[QaTraceEntry]


@dataclass
class QaPipeline:
    """一次问答的编排器。settings/index 由调用方注入，LLM 惰性创建。"""

    settings: Settings
    index: IndexStore
    llm_settings: LlmSettings | None = None
    top_k: int = 5
    model: BaseChatModel | None = None
    _trace: list[QaTraceEntry] = field(default_factory=list)

    def answer_question(self, question: str) -> QaResponse:
        if not question.strip():
            raise ValueError("question must not be blank")
        # 改写钩子（C-2）：PARTIAL 且 len(self._trace) < _MAX_ROUNDS 时可在此插入
        #   rewrite(question, chunks) → 新查询 → 重检索 → 重新判定。
        # M2 不启用——基线数据显示 6 道需改写题原问题全部命中 top-5，
        # 改写收益无数据支撑（C-3：启用时以 _MAX_ROUNDS 为循环上限）。
        chunks = retrieve(question, self.settings, self.index, top_k=self.top_k)
        verdict = self.judge(question, chunks)
        self._trace.append(QaTraceEntry(
            round_index=1, query=question,
            retrieved=[
                {"chunk_id": c.chunk_id, "document_id": c.document_id, "score": c.score, "rank": rank}
                for rank, c in enumerate(chunks, start=1)
            ],
            decision=verdict,
        ))
        if verdict == "NONE":
            # 不变量 4：拒答不进入生成，不编造，不拿低分片段凑数
            return QaResponse(answer=_REFUSAL_ANSWER, status="REFUSED", chunk_ids=[], trace=self._trace)
        answer = self.generate(question, chunks, partial=(verdict == "PARTIAL"))
        return QaResponse(
            answer=answer,
            status="ANSWERED" if verdict == "SUFFICIENT" else "PARTIAL",
            chunk_ids=sorted({c.chunk_id for c in chunks}),
            trace=self._trace,
        )

    # ---- LLM 两跳：判定与生成分开的理由是分流逻辑必须确定，不能让生成
    #      顺带"感觉内容不够"——那是不可测的隐性判定 ----

    def judge(self, question: str, chunks: list[RetrievedChunk]) -> JudgeVerdict:
        """三态判定（判断力记录 #15 的三条判据）。

        1 和 3 是护栏，2 是核心：把片段单独交给 LLM 问"仅凭这些能否完整回答"。
        """
        numbered = "\n\n".join(
            f"[{rank}] {chunk.text}" for rank, chunk in enumerate(chunks, start=1))
        prompt = (
            "你是知识库问答的判定器。根据下面的检索片段判断：仅凭这些内容，"
            "能否完整回答用户的问题？\n\n"
            f"问题：{question}\n\n检索片段：\n{numbered}\n\n"
            "判定规则：\n"
            "- SUFFICIENT：片段中有与问题主题直接相关的内容，且足以支撑完整回答\n"
            "- PARTIAL：有直接相关内容，但只覆盖问题的一部分，缺失的是问题主体而非边角\n"
            "- NONE：没有与问题主题直接相关的片段（词面相似不算直接相关）\n\n"
            '只输出 JSON：{"verdict": "SUFFICIENT"}（三选一），不要输出其他内容。'
        )
        response = self._invoke_model(prompt)
        match = _VERDICT_PATTERN.search(response)
        if match is None:
            raise QaError(f"judge output is not parseable JSON: {response[:120]!r}")
        try:
            verdict = json.loads(match.group(0))["verdict"]
        except (json.JSONDecodeError, KeyError) as exc:
            raise QaError(f"judge output missing verdict: {response[:120]!r}") from exc
        if verdict not in ("SUFFICIENT", "PARTIAL", "NONE"):
            raise QaError(f"judge verdict is not a known value: {verdict!r}")
        return verdict  # type: ignore[return-value]

    def generate(self, question: str, chunks: list[RetrievedChunk], *, partial: bool) -> str:
        """带 [n] 引用的生成。n 对下方片段编号；禁止编造未给出的内容。"""
        numbered = "\n\n".join(
            f"[{rank}] {chunk.text}" for rank, chunk in enumerate(chunks, start=1))
        boundary = ""
        if partial:
            boundary = (
                "\n注意：以上片段只覆盖了问题的一部分。回答时先基于已有内容作答，"
                "并明确声明覆盖边界（说明库中只有这些相关内容，未覆盖的部分无法回答）。"
            )
        prompt = (
            "根据下面的知识库片段回答问题。要求：\n"
            "1. 只使用片段中出现的信息，不得编造或补充片段之外的知识\n"
            "2. 每个事实性结论后用 [n] 标注来源片段编号\n\n"
            f"问题：{question}\n\n片段：\n{numbered}{boundary}"
        )
        return self._invoke_model(prompt)

    def _invoke_model(self, prompt: str) -> str:
        if self.model is None:
            self.model = create_chat_model(self.llm_settings)
        response = self.model.invoke([HumanMessage(content=prompt)])
        content = response.content
        if not isinstance(content, str) or not content.strip():
            raise QaError("model returned empty or non-text content")
        return content
