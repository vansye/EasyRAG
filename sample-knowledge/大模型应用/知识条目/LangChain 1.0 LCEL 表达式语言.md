---
type: knowledge
title: LangChain 1.0 LCEL 表达式语言
created: 2026-09-01
updated: 2026-09-01
tags: [LangChain, LCEL, 表达式语言, Runnable, 声明式编程]
source:
conclusion:
publish: false
description:
slug: langchain-lcel
---

## 详细

### 概念

**LCEL（LangChain Expression Language，LangChain 表达式语言）** 是 LangChain 1.0 引入的核心语法，用于以**声明式**的方式组合和构建 LLM 工作流。它是 LangChain 1.0 最重要的创新之一，标志着框架从松散的组件拼装转向了标准化、声明式的链式编程模式。

LCEL 的本质是一套基于**管道操作符（`|`）** 的语法，用于将实现了 **Runnable 接口** 的组件（如提示词模板、模型、输出解析器等）串联成一条“链”（Chain）。它的核心思想是 **“描述应该发生什么，而不是如何发生”** ，让开发者用更简洁、更 Pythonic 的方式构建复杂的 AI 应用。

### 重点

- **Runnable 接口与管道操作符**：LCEL 的基石是 **Runnable 协议**。LangChain 1.0 中几乎所有核心组件——提示词模板（`ChatPromptTemplate`）、模型（`ChatOpenAI`）、输出解析器（`StrOutputParser`）等——都实现了这个接口。它们都拥有统一的 `invoke`、`stream`、`batch` 等调用方法。管道操作符 `|` 则将上一个 Runnable 的输出作为下一个 Runnable 的输入，从而串联成链。例如：`chain = prompt | model | output_parser`。

- **核心优势**：LCEL 带来了多项关键能力：
  - **原生流式传输（Streaming）**：用 LCEL 构建的链天然支持流式输出，可以逐字返回结果，优化用户体验。
  - **自动并行执行**：当链中有可以并行的分支时（如同时检索多个数据源），LCEL 会自动优化并行执行，显著降低延迟。
  - **异步与批量支持**：所有 LCEL 链都原生支持 `ainvoke`、`abatch` 等异步和批量操作，便于处理高并发场景。
  - **无缝可观测性**：LCEL 链中的所有步骤会自动集成 LangSmith 追踪，方便调试和监控。

- **适用场景与边界**：LCEL 最适合**线性或简单分支的编排任务**，例如标准的 RAG 流程（检索 → 生成）或问答链。当应用需要复杂的状态管理、多轮循环、条件分支或多智能体协作时，官方推荐使用 **LangGraph** 进行流程编排。两者可以结合使用：用 LangGraph 管理宏观流程，在每个节点内部用 LCEL 构建具体逻辑。