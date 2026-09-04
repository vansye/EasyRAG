---
type: knowledge
title: Interceptor 拦截器
created: 2026-07-24T00:00:00
updated: 2026-08-30T00:00:00
tags: [JavaWeb, SpringMVC, 入门]
source:
conclusion:
---

## 详细

### 概念

Interceptor 是 Spring MVC 中常见的拦截机制，主要在控制器方法执行前后做统一处理。它比 [[Filter过滤器]] 更贴近 Spring 框架，适合在进入 Controller 前做登录验证、权限判断，也可以在响应完成后做收尾处理。两者都是请求处理链上的拦截组件，常被拿来对比学习。


### 重点

- 作用范围通常在 Spring MVC 请求链中
- 常见方法有 preHandle、postHandle、afterCompletion
- 适合和 Handler、Controller 配合理解
- 详细讲解见 Interceptor拦截器技术（含 token 登录拦截器代码示例、与 Filter 的区别）