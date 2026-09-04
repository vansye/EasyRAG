---
type: knowledge
title: SQL 注入
created: 2026-07-24T00:00:00
updated: 2026-07-24T00:00:00
tags: [JavaWeb, 安全, 入门]
source:
conclusion:
---

## 详细

### 概念

SQL 注入是一种常见安全问题，攻击者通过拼接恶意输入影响原本的 SQL 语句执行结果。JavaWeb 中如果直接把用户输入拼进 SQL，风险会很高，可能导致数据被非法查询、修改甚至删除。


### 重点

- 不要直接拼接用户输入到 SQL 字符串
- 优先使用预编译语句和参数绑定
- MyBatis 和 JDBC 都要注意输入校验