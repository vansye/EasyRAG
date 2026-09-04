---
type: knowledge
title: Vue onMounted 生命周期钩子
created: 2026-07-30
updated: 2026-07-30
tags:
  - Vue
  - 生命周期
  - 钩子函数
source: 无
conclusion: onMounted 是 Vue 3 Composition API 中的生命周期钩子，在组件完成初始渲染并挂载到 DOM 后执行，是执行 DOM 操作和发起异步请求的最佳时机。
---

## 详细

### 概念
`onMounted` 是 Vue 3 组合式 API（Composition API）提供的生命周期钩子函数，用于在组件被挂载到 DOM 后执行指定的回调函数。它等价于 Vue 2 Options API 中的 `mounted` 选项。

### 重点
- **执行时机**：组件完成初始渲染、真实 DOM 已创建并插入页面后执行（仅执行一次）
- **常见用途**：执行 DOM 操作（访问或修改元素）、发起异步请求（如调用 API 获取初始数据）、初始化第三方库（如 ECharts 图表、地图、编辑器等）
- **Vue 2 vs Vue 3 写法**：
  - Vue 2 Options API：`mounted() { ... }`
  - Vue 3 Composition API：`onMounted(() => { ... })`，需从 `vue` 导入，并在 `setup()` 中同步注册（如 `<script setup>`）
- **与 `created` / `setup` 的区别**：`setup` 执行时 DOM 尚未生成，无法操作 DOM；`onMounted` 保证 DOM 已存在
- **执行顺序**：父组件的 `onMounted` 会在所有子组件挂载完成后执行
- **SSR 注意**：在服务端渲染（SSR）环境下，`onMounted` 不会执行（仅在客户端执行）
- **结合异步请求**：虽然 `onMounted` 本身不支持 `async/await`，但可在回调内部使用异步函数发起 API 请求