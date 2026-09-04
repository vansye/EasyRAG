---
type: knowledge
title: Bean
created: 2026-07-24T00:00:00
updated: 2026-07-24T00:00:00
tags: [JavaWeb, Spring, 入门]
source:
conclusion:
---

## 详细

### 概念

Bean 通常指被 Spring 容器管理的对象。只要对象交给容器创建、装配和管理，它就可以被称为 Bean。Bean 是 Spring 中最常见的术语之一，很多配置和注解都围绕它展开。


### 重点

- Bean 不是普通的 Java 类名，而是容器管理对象的称呼
- 创建、注入、销毁都可以由容器接管
- 在配置类、注解和 XML 中都可能出现
- 详细讲解见 IOC与DI依赖注入（含 Bean 的创建方式、作用域、生命周期）