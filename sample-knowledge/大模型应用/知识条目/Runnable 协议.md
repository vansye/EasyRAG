---
type: knowledge
title: LangChain 1.0 Runnable 协议
created: 2026-09-01
updated: 2026-09-01
tags: [LangChain, Runnable, 统一接口, 协议, 组合]
source:
conclusion:
publish: false
description:
slug: langchain-runnable
---

## 详细

### 概念

**Runnable 协议** 是 LangChain 1.0 的底层统一接口标准，也是整个框架的基石。它规定所有可被“运行”的组件都必须实现相同的调用接口，从而实现“**一切皆 Runnable**”的设计理念。

无论是大语言模型、提示词模板、输出解析器、检索器，还是工具、智能体，甚至 LangGraph 中的图节点，都遵循这一协议。Runnable 协议让不同功能的组件能够以统一的方式被调用、组合和编排，是 LCEL（LangChain 表达式语言）能够运作的基础。

### 重点

- **统一的核心方法**：所有 Runnable 组件都提供三组标准方法，覆盖同步和异步场景：
  - **invoke / ainvoke**：单次调用，接收一个输入，返回一个输出。
  - **stream / astream**：流式调用，逐块产出结果，适用于实时响应场景。
  - **batch / abatch**：批量调用，高效处理多个输入，默认并行执行以提升吞吐量。

- **组合方式**：Runnable 最大的价值在于可组合性：
  - **顺序组合（RunnableSequence）**：通过管道操作符 `|` 将多个 Runnable 串联，上一个的输出自动成为下一个的输入。例如：`chain = prompt | model | output_parser`。
  - **并发组合（RunnableParallel）**：将多个 Runnable 并行执行，共享同一输入，适合同时从多个数据源获取信息的场景。

- **核心优势**：
  - **标准化**：只需学习一套 API，即可操作所有组件，大幅降低学习成本。
  - **内置优化**：基于 Runnable 构建的链自动获得并行执行、异步支持、流式传输等能力。
  - **可观测性**：所有执行过程自动集成 LangSmith 追踪，便于调试和监控。
  - **灵活扩展**：任何自定义函数或第三方组件都可以通过实现 Runnable 接口无缝融入 LangChain 生态。