---
type: knowledge
title: invoke、stream 与 batch
created: 2026-08-28
updated: 2026-08-28
tags:
  - LangChain
  - LLM
  - invoke
  - stream
  - batch
  - 模型调用
source:
conclusion:
publish: false
description:
slug: langchain-invoke-stream-batch
---

## 详细

### 概念

在 LangChain 1.0 中，`invoke`、`stream` 和 `batch` 是与聊天模型（Chat Model）交互的**三种标准调用方法**，统一封装在 `BaseChatModel` 基类中。无论使用哪个提供商的模型（OpenAI、Anthropic 等），都可以通过这三个方法执行调用，它们分别对应同步请求、流式响应和批量处理场景，构成了 LangChain 1.0 模型调用的核心 API。

这三种方法接收相同的输入（消息列表或结构化提示），但返回方式不同，允许开发者根据应用需求选择最合适的交互模式。

### 重点

- **invoke（同步调用）**：最基础的调用方式，传入消息列表，等待模型完整生成回复后一次性返回完整的 `AIMessage` 对象。适用于常规问答、单轮生成等无需实时反馈的场景。调用示例：`response = model.invoke(messages)`。

- **stream（流式调用）**：用于需要逐字或逐块输出内容的场景，如聊天打字机效果。调用后返回一个迭代器，每次产出 `AIMessageChunk` 对象，包含了增量文本和可能的元数据。开发者可以边生成边向用户展示，显著提升交互体验。调用示例：`for chunk in model.stream(messages): print(chunk.text, end="")`。

- **batch（批量调用）**：一次性传入多条独立的输入消息列表（列表的列表），模型会并行或批量处理这些请求，返回一个与输入数量对应的 `AIMessage` 列表。适用于离线处理、评测、大规模数据生成等场景，能有效提高吞吐量。调用示例：`responses = model.batch([messages1, messages2, messages3])`。

- **统一性与演进**：这三种方法在 LangChain 1.0 中已成为标准接口，不仅适用于聊天模型，也扩展到了链（Chain）和智能体（Agent）等高级组件。LangChain 1.0 强化了这些方法的异步版本（`ainvoke`、`astream`、`abatch`），方便在异步框架中使用。