---
type: 学习笔记
title: create_agent 与 Agent 输入输出
created: 2026-09-03
updated: 2026-09-03
subject: 大模型应用
tags:
  - LangChain
  - Agent
  - create_agent
  - 输入输出
  - 工具调用
  - messages
---

> `create_agent()` 是 LangChain 1.0 构建 Agent 的唯一入口，取代了旧版的 `create_react_agent`。理解它的参数含义、输入格式和输出结构，是把 Agent 接入生产系统的第一步。

## 目录

- [1. 从旧版迁移：`create_react_agent` → `create_agent`](#1-从旧版迁移create_react_agent--create_agent)
- [2. `create_agent` 参数详解](#2-create_agent-参数详解)
  - [2.1 必选参数](#21-必选参数)
  - [2.2 可选参数](#22-可选参数)
- [3. Agent 的输入：`invoke()` 接收什么](#3-agent-的输入invoke-接收什么)
  - [3.1 输入形态一：字符串](#31-输入形态一字符串)
  - [3.2 输入形态二：消息列表](#32-输入形态二消息列表)
  - [3.3 输入形态三：dict（传入 system_prompt 覆盖）](#33-输入形态三dict传入-system_prompt-覆盖)
- [4. Agent 的输出：`invoke()` 返回什么](#4-agent-的输出invoke-返回什么)
  - [4.1 输出类型：MessagesDict](#41-输出类型messagesdict)
  - [4.2 输出内容结构](#42-输出内容结构)
  - [4.3 从输出中提取最终答案](#43-从输出中提取最终答案)
- [5. 完整实战：多工具 Agent](#5-完整实战多工具-agent)
- [6. 输入输出全流程图](#6-输入输出全流程图)
- [小结](#小结)

---

## 1. 从旧版迁移：`create_react_agent` → `create_agent`

LangChain 1.0 统一了 Agent 构建入口，旧写法需迁移：

```python
# ❌ 旧版（langgraph.prebuilt，已弃用）
from langgraph.prebuilt import create_react_agent
agent = create_react_agent(model, tools, prompt=prompt)

# ✅ 新版（langchain.agents）
from langchain.agents import create_agent
agent = create_agent(model, tools, system_prompt="...")
```

**主要变更对照：**

| 变更项 | 旧版 | 新版 |
|--------|------|------|
| 导入路径 | `langgraph.prebuilt` | `langchain.agents` |
| 函数名 | `create_react_agent` | `create_agent` |
| 参数名 | `prompt=prompt` | `system_prompt="..."` |
| 中间件 | `pre_model_hook` / `post_model_hook` | `middleware=[...]` |
| 返回值 | AgentState（带 history 字段） | MessagesDict（标准消息 dict） |

---

## 2. `create_agent` 参数详解

### 2.1 必选参数

```python
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain.tools import tool

@tool
def get_weather(city: str) -> str:
    """查询指定城市的当前天气"""
    return f"{city}今天晴，气温 25°C"

model = init_chat_model(model="openai:gpt-4o", temperature=0)
tools = [get_weather]

agent = create_agent(
    model=model,       # 必选：LLM 模型实例
    tools=tools,       # 必选：工具列表
    system_prompt="你是助手...",  # 必选：系统提示词
)
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `model` | `BaseChatModel` | 驱动 Agent 的 LLM，温度建议设为 0（决策任务） |
| `tools` | `list[BaseTool]` | Agent 可调用的工具集合，空列表意味着无工具 |
| `system_prompt` | `str` | 系统提示词，定义 Agent 的角色和行为准则 |

### 2.2 可选参数

```python
agent = create_agent(
    model=model,
    tools=tools,
    system_prompt="...",

    # ── 循环控制 ───────────────────────────────────
    max_iterations=10,            # 最多循环多少次，防止无限循环
    interrupt_before=[],          # 在哪些步骤前暂停（用于人机协同）
    interrupt_after=[],           # 在哪些步骤后暂停
    auto_invoke_model=False,      # 是否自动调用模型（默认 True）

    # ── 错误处理 ───────────────────────────────────
    handle_parsing_errors=True,   # 解析错误时继续而非崩溃
    handle_tool_errors=True,      # 工具错误时把错误信息回传 LLM
    retry_on_tool_error=True,     # 工具失败时自动重试

    # ── 中间件 ─────────────────────────────────────
    middleware=[],                # 可选中间件列表，见下方
)
```

#### 常用中间件（middleware）

```python
from langchain_core.middleware import (
    ConversationSummaryMiddleware,
    ToolFilterMiddleware,
    PiiMaskingMiddleware,
)

agent = create_agent(
    model=model,
    tools=tools,
    system_prompt="...",
    middleware=[
        PiiMaskingMiddleware(),                    # PII 脱敏
        ToolFilterMiddleware(allowed_tools=["get_weather"]),  # 只允许调用天气工具
    ],
)
```

---

## 3. Agent 的输入：`invoke()` 接收什么

### 3.1 输入形态一：字符串

最简形式，Agent 自动包装成 `HumanMessage`：

```python
result = agent.invoke("北京今天天气怎么样？")
# 等价于：
# result = agent.invoke([HumanMessage("北京今天天气怎么样？")])
```

### 3.2 输入形态二：消息列表

推荐形式，可以包含完整对话历史：

```python
from langchain.messages import SystemMessage, HumanMessage, AIMessage

messages = [
    SystemMessage(content="你是一个 weather assistant。"),
    HumanMessage(content="上海今天天气？"),
    AIMessage(content="上海今天晴，22°C。"),
    HumanMessage(content="那北京呢？"),  # 多轮追问
]

result = agent.invoke(messages)
```

### 3.3 输入形态三：dict（传入额外变量）

用于需要填充 Prompt 模板变量的场景：

```python
# 如果 system_prompt 里使用了 {username} 这样的模板变量
result = agent.invoke({
    "messages": [HumanMessage("你好")],
    "username": "张三",
})
```

### 3.4 输入总结

```
agent.invoke(...)
        │
        ├── 字符串    → 自动包装为 [HumanMessage("...")]
        ├── 消息列表  → 直接使用，支持多轮对话
        └── dict      → 展开变量，messages 字段必须是消息列表
```

---

## 4. Agent 的输出：`invoke()` 返回什么

### 4.1 输出类型：MessagesDict

Agent 的 `invoke()` 返回的是一个 **`MessagesDict`** 对象（本质是 dict），包含完整的消息历史：

```python
result = agent.invoke("北京天气怎样？")
print(type(result))  # <class 'dict'>
print(result.keys())  # dict_keys(['messages'])
```

### 4.2 输出内容结构

```python
{
    "messages": [
        SystemMessage("你是一个 weather assistant。"),  # 系统提示
        HumanMessage("北京天气怎样？"),                  # 用户输入
        AIMessage(                                       # 模型第一次回复（可能含工具调用）
            tool_calls=[{
                "name": "get_weather",
                "args": {"city": "北京"},
                "id": "call_xyz123"
            }]
        ),
        ToolMessage(                                      # 工具执行结果
            content="北京今天晴，25°C",
            tool_call_id="call_xyz123"
        ),
        AIMessage(                                       # 模型最终回复
            content="北京今天晴，25°C，注意防晒。"
        ),
    ]
}
```

### 4.3 从输出中提取最终答案

```python
result = agent.invoke("北京天气怎样？")

# 方式一：取最后一条 AIMessage 的文本
final_answer = result["messages"][-1].content.text
print(final_answer)  # "北京今天晴，25°C，注意防晒。"

# 方式二：遍历所有消息，找出最终答案
for msg in reversed(result["messages"]):
    if isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
        final_answer = msg.content.text
        break

# 方式三：获取完整对话历史（用于多轮对话）
conversation_history = result["messages"]
```

### 4.4 输出与输入的区别

| 维度 | 输入 | 输出 |
|------|------|------|
| 类型 | 字符串 / 消息列表 / dict | `MessagesDict`（dict） |
| 内容 | 用户消息 | 完整对话历史（含工具调用过程） |
| 用途 | 发起一次 Agent 调用 | 提取最终答案 / 保存对话历史 |

---

## 5. 完整实战：多工具 Agent

```python
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain.tools import tool
from langchain.messages import HumanMessage, AIMessage

# ── 定义工具 ────────────────────────────────────────────────
@tool
def get_weather(city: str) -> str:
    """查询指定城市的当前天气"""
    return f"{city}今天晴，气温 25°C，风力 3 级"

@tool
def calculate(expression: str) -> float:
    """计算数学表达式"""
    return eval(expression)

@tool
def search_web(query: str) -> str:
    """搜索互联网获取最新信息"""
    return f"[搜索结果] {query} 的最新资讯..."

# ── 创建 Agent ──────────────────────────────────────────────
model = init_chat_model(model="openai:gpt-4o", temperature=0)

agent = create_agent(
    model=model,
    tools=[get_weather, calculate, search_web],
    system_prompt="""你是一个全能助手。
- 天气查询 → 使用 get_weather
- 数学计算 → 使用 calculate
- 需要实时信息 → 使用 search_web
- 工具结果不足以回答时，诚实说明""",
    max_iterations=5,
    handle_tool_errors=True,
)

# ── 调用方式一：字符串输入 ──────────────────────────────────
result1 = agent.invoke("北京天气怎么样？算一下 123 * 456")
print(result1["messages"][-1].content.text)
# "北京今天晴，25°C，风力 3 级。123 * 456 = 56088。"

# ── 调用方式二：消息列表（多轮对话）────────────────────────
messages = [
    HumanMessage("上海天气怎样？"),
]
result2 = agent.invoke(messages)
last_msg = result2["messages"][-1]
print(last_msg.content.text)
# "上海今天晴，25°C，风力 3 级。"

# 携带历史继续多轮对话
messages.append(last_msg)  # 追加 AI 回复
messages.append(HumanMessage("那北京呢？"))
result3 = agent.invoke(messages)
print(result3["messages"][-1].content.text)
# "北京今天晴，25°C，风力 3 级。"
```

---

## 6. 输入输出全流程图

```
┌─────────────────────────────────────────────────────────────────┐
│                      Agent 完整数据流                            │
│                                                                 │
│  ① 开发者调用                                                 │
│     agent.invoke("北京天气怎样？")                              │
│            │                                                    │
│            ▼                                                    │
│  ② Agent 内部包装                                              │
│     messages = [SystemMessage(...),                             │
│                 HumanMessage("北京天气怎样？")]                 │
│            │                                                    │
│            ▼                                                    │
│  ③ LLM 调用                                                    │
│     model.invoke(messages)                                      │
│            │                                                    │
│            ▼                                                    │
│  ④ LLM 返回 AIMessage（含 tool_calls）                         │
│     {"name": "get_weather", "args": {"city": "北京"}}           │
│            │                                                    │
│            ▼                                                    │
│  ⑤ 执行工具                                                    │
│     tool.invoke({"city": "北京"}) → "北京今天晴，25°C"          │
│            │                                                    │
│            ▼                                                    │
│  ⑥ 追加 ToolMessage，回到 ③                                    │
│     messages.append(ToolMessage("北京今天晴，25°C"))            │
│            │                                                    │
│            ▼                                                    │
│  ⑦ LLM 综合结果，返回 final_answer                             │
│     AIMessage(content="北京今天晴，25°C。")                     │
│            │                                                    │
│            ▼                                                    │
│  ⑧ 返回结果                                                     │
│     {"messages": [SystemMessage, HumanMessage,                 │
│                   AIMessage(tool_calls),                        │
│                   ToolMessage,                                  │
│                   AIMessage(final_answer)]}                     │
│            │                                                    │
│            ▼                                                    │
│  ⑨ 开发者提取                                                   │
│     result["messages"][-1].content.text                         │
│            │                                                    │
│            ▼                                                    │
│     "北京今天晴，25°C。"                                        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 小结

- **`create_agent()`** 是 LangChain 1.0 构建 Agent 的唯一入口，取代了旧版 `create_react_agent`
- **三个必选参数**：`model`（LLM）、`tools`（工具列表）、`system_prompt`（系统提示词）
- **输入形式**：字符串（自动包装）/ 消息列表（多轮对话）/ dict（带模板变量）
- **输出类型**：`MessagesDict`（dict），包含完整对话历史，通过 `result["messages"][-1].content.text` 提取最终答案
- **循环控制**：通过 `max_iterations` 防止无限循环，通过 `handle_tool_errors` 控制错误恢复策略
- **核心认知**：Agent 的 invoke 返回的是**完整消息历史**，不是单一答案——这是与 Chain 的关键区别

<!-- KB:ANNOTATIONS -->
