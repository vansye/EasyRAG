---
type: knowledge
title: LangChain 1.0 PromptTemplate 提示词模板
created: 2026-09-01
updated: 2026-09-01
tags: [LangChain, PromptTemplate, 提示词模板, 模板格式化, LLM]
source:
conclusion:
publish: false
description:
slug: langchain-prompttemplate
---

## 详细

### 概念

`PromptTemplate` 是 LangChain 中最基础的**提示词模板类**，用于生成**文本类型**的提示词。它本质上是一个包含占位符的字符串模板，通过定义模板字符串和占位符变量，在运行时将具体值填充进去，最终生成一个完整的字符串提示词。

与 `ChatPromptTemplate`（专门用于消息列表）不同，`PromptTemplate` 适用于需要返回纯文本字符串的场景。LangChain 1.0 将其定位为所有提示词组件的基础类，与其他组件一样，基础接口得到了统一。

### 重点

- **核心用法**：创建一个包含占位符的字符串模板，然后调用 `format()` 方法传入变量值，生成最终的提示词字符串。
  ```python
  from langchain.prompts import PromptTemplate

  template = "请根据以下内容回答问题：{context}\n问题：{question}"
  prompt = PromptTemplate(template=template, input_variables=["context", "question"])
  formatted_prompt = prompt.format(context="这是一段背景信息。", question="核心观点是什么？")
  ```
- **关键特性**：
    
    - **部分变量（Partial Variables）**：允许预先固定某些变量值，生成一个"部分填充"的新模板。这在需要复用同一模板但某些变量值固定时非常有用。
        
    - **模板格式（Template Format）**：支持多种模板语法，如 Python 的 `.format()`、Jinja2 以及 F-string 等。默认使用 `.format()` 风格。
        
    - **输入变量验证**：在创建模板时会验证 `input_variables` 是否与模板中的占位符匹配，提供早期错误反馈。
        
- **与 1.0 版本的关系**：在 LangChain 1.0 中，`PromptTemplate` 的导入路径和基础接口保持一致，但整体定位变得更加清晰——它被保留为专门处理纯文本提示词的组件，而所有涉及消息角色的场景统一由 `ChatPromptTemplate` 处理。两者可以配合使用，例如先用 `PromptTemplate` 生成文本内容，再将其封装为 `HumanMessage` 放入消息列表。