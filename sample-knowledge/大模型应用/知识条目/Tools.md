---
type: knowledge
title: LangChain 1.0 工具（Tools）
created: 2026-09-04
updated: 2026-09-04
tags: [LangChain, Tools, 工具调用, Agent, 函数调用]
source:
conclusion:
publish: false
description:
slug: langchain-tools
---

## 详细

### 概念

在 LangChain 中，**工具（Tool）** 是将一个 Python 函数与其元数据（名称、描述、参数模式）关联起来的抽象。它是 LLM 应用与外部世界交互的接口，让模型能够执行搜索、查询数据库、调用 API 等操作。

工具可以传递给支持**工具调用（Tool Calling）** 的聊天模型，使模型能够在适当时“请求”执行某个函数。LangChain 中的所有工具都实现了 **Runnable 协议**，因此可以像其他组件一样通过 LCEL 进行编排。

### 重点

- **核心概念**：工具封装了三个关键信息：
  - **name**：工具的名称。
  - **description**：工具功能的描述。
  - **args**：工具参数的 JSON Schema（由函数的类型注解自动推断）。
  工具调用允许模型根据输入的相关性决定是否以及如何调用工具。

- **工具创建**：推荐使用 `@tool` 装饰器创建工具。被装饰的函数会自动转换为工具对象，获得 `.invoke()` 等方法。例如：
```python
  from langchain_core.tools import tool

  @tool
  def multiply(a: int, b: int) -> int:
      """Multiply two numbers."""
      return a * b
```
除装饰器外，也可以通过继承 `BaseTool` 或使用 `StructuredTool` 创建工具。

- **工具绑定与调用**：工具需要通过 `.bind_tools()` 方法绑定到支持工具调用的模型。模型被调用后，如果决定使用工具，会在 `AIMessage` 中返回工具调用参数，这些参数可以直接传递给工具执行。
    
- **工具与 Agent**：工具是 Agent 的核心能力来源。在 LangChain 1.0 中，通过 `create_agent` 创建 Agent 时，将工具列表传入 `tools` 参数即可。Agent 会在循环中调用模型、执行工具，直到完成任务。
    
- **LangChain 1.0 的变化**：
    
    - **命名空间精简**：`langchain.tools` 模块重新从 `langchain-core` 导出 `@tool`、`BaseTool` 等核心工具组件。
        
    - **工具序列化变更**：LangChain 1.0 改变了工具的序列化方式，可能影响与 LangGraph 等下游工具的兼容性。