---
type: 学习笔记
title: Pinia状态管理
created: 2026-07-28
updated: 2026-07-28
tags:
  - Pinia
  - 状态管理
  - Store
  - defineStore
  - 响应式
subject: JavaWeb
description: 详细讲解 Pinia 状态管理的完整实现，包括 Store 定义、状态管理、多 Store 架构、持久化以及与路由和 API 的配合。
---

> 本笔记以 Tlias 管理系统前端为例，系统讲解 Pinia 状态管理的完整实现方案。从 Store 定义到状态管理，从多 Store 架构到持久化，每个细节都深入剖析。

## 目录

- [1. 为什么需要状态管理](#1-为什么需要状态管理)
- [2. Pinia 简介](#2-pinia简介)
  - [2.1 Pinia vs Vuex](#21-pinia-vs-vuex)
  - [2.2 核心概念](#22-核心概念)
- [3. Store 定义](#3-store定义)
  - [3.1 defineStore 基本用法](#31-definestore-基本用法)
  - [3.2 state 状态定义](#32-state-状态定义)
  - [3.3 actions 业务方法](#33-actions-业务方法)
  - [3.4 getters 计算属性](#34-getters-计算属性)
- [4. 使用 Store](#4-使用-store)
- [5. 多 Store 架构](#5-多-store-架构)
  - [5.1 user Store](#51-user-store)
  - [5.2 employee Store](#52-employee-store)
- [6. 状态持久化](#6-状态持久化)
- [7. Store 与 API 的配合](#7-store-与-api-的配合)
- [8. Store 与路由的配合](#8-store-与-rout的配合)
- [9. 完整 Store 示例](#9-完整-store-示例)
- [10. 常见用法总结](#10-常见用法总结)
- [小结](小结)

---

## 1. 为什么需要状态管理

在 Vue 应用中，组件之间的数据共享有多种方式：

| 方式 | 适用场景 | 缺点 |
|------|----------|------|
| **props** | 父子组件通信 | 多层传递时变成"prop 穿井" |
| **events** | 子组件通知父组件 | 复杂场景难以维护 |
| **provide/inject** | 祖孙组件通信 | 不适用于频繁变化的数据 |
| **Vuex/Pinia** | **全局共享状态** | 需要额外学习成本 |

**需要全局共享的状态**：
- 用户信息（登录态、token、用户名）
- 部门列表（多个页面都要用）
- 员工下拉数据（员工选择器要用）
- 分页信息（列表页的当前页码、搜索条件）
- 全局 loading 状态

这些问题正是状态管理库要解决的：**集中管理跨组件共享的状态**。

---

## 2. Pinia 简介

Pinia 是 Vue3 官方推荐的状态管理库，替代了 Vuex。特点：

- **轻量**：~1KB，比 Vuex 小很多
- **TypeScript 支持**：原生支持，无需额外配置
- **模块化**：不用像 Vuex 一个大 store，可以分模块
- **插件系统**：支持持久化、devtools 等插件
- **API 简洁**：`defineStore` 比 Vuex 的 `createStore` 更简单

### 2.1 Pinia vs Vuex

| 特性 | Pinia | Vuex |
|------|-------|------|
| **体积** | ~1KB | 较大 |
| **TypeScript** | 原生支持 | 需要额外配置 |
| **API** | `defineStore()` | `new Vuex.Store({})` |
| **模块化** | 天然模块化 | 需要 modules 选项 |
| **组合式 API** | 完美支持 | 需要 Vuex 4+ |
| **Vue 3 推荐** | ✅ | ❌ (Vuex 4 为最后版本) |

**新项目推荐用 Pinia**，旧项目可继续使用 Vuex 4。

### 2.2 核心概念

| 概念 | 说明 | 对应 Vuex |
|------|------|-----------|
| **Store** | 状态容器 | Store/State |
| **state** | 响应式数据存储 | State |
| **actions** | 业务方法（可异步） | Actions |
| **getters** | 计算属性 | Getters |
| **devtools** | 浏览器调试工具 | Vuex Devtools |

---

## 3. Store 定义

### 3.1 defineStore 基本用法

`defineStore()` 是创建 Store 的唯一入口函数，有两个参数：

```javascript
# 定义式：带 name 的命名 Store（推荐）
export const useCounterStore = defineStore('counter', () => {
  # state、actions 等定义在这里
})

# 可选：无名的逻辑 Store（适合工具函数）
export const useApiStore = defineStore(() => {
  # 只定义 actions，没有 state
})
```

**第一个参数（store ID）**：
- 字符串，用于 Vue Devtools 识别
- 必须唯一（全局唯一）
- 格式：`useStoreName` 中的 `StoreName`（如 `userStore` 的 ID 是 `user`）

**第二个参数**：
- 可以是配置对象（带 state、actions、getters）
- 也可以是返回对象的函数（更简洁，推荐）

### 3.2 state 状态定义

state 是 Store 的响应式数据存储，必须是函数形式返回初始对象：

```javascript
export const useUserStore = defineStore('user', {
  state: () => ({    # 必须是函数！
    userInfo: null,   # 用户信息
    token: '',        # Token
    departments: [],  # 部门列表
    allEmployees: []  # 全部员工列表
  })
})
```

**为什么 state 必须是函数**？
- Pinia 允许实例化多个 Store（如测试时）
- 每个实例都有独立的 state
- 如果直接写对象，所有实例共享同一个 state，会出错

**state 的特点**：
- 自动响应式（基于 ref/reactive）
- 修改后自动触发组件更新
- 可以通过 Devtools 查看和修改

### 3.3 actions 业务方法

actions 是 Store 的方法，可以修改 state、发起 API 请求、执行业务逻辑：

```javascript
actions: {
  # 登录方法（可异步）
  async login(username, password) {
    const result = await loginApi.login({ username, password }) # 调用 API
    this.token = result.token                                 # 修改 state
    this.userInfo = { ... }                                   # 修改 state
    localStorage.setItem('token', this.token)                 # 持久化
  },

  # 登出方法
  logout() {
    this.token = null
    this.userInfo = null
    localStorage.removeItem('token')
  },

  # 获取部门列表
  async loadDepartments() {
    this.departments = await deptApi.list()
  }
}
```

**actions 的特点**：
- 可以访问 `this`（指向 Store 实例）
- 可以异步（async/await）
- 可以修改 state
- 可以调用其他 actions
- **不能直接作为计算属性使用**（要用 getters）

### 3.4 getters 计算属性

getters 类似于 Vue 的 computed，用于基于 state 计算衍生值：

```javascript
export const useUserStore = defineStore('user', {
  state: () => ({ departments: [] }),
  
  getters: {
    # 计算部门数量
    departmentCount(state) {
      return state.departments.length
    },
    
    # 计算是否有部门
    hasDepartments(state) {
      return state.departments.length > 0
    }
  }
})
```

**用法**：

```vue
<script setup>
import { useUserStore } from '@/stores/user'
const userStore = useUserStore()

# 直接访问，像属性一样
userStore.departmentCount   # 部门数量
userStore.hasDepartments    # 是否有部门
</script>
```

**getters 特点**：
- 自动缓存（依赖的 state 不变时，结果不变）
- 可以访问其他 getters
- 第一个参数是 state（访问 state）
- 第二个参数是 rootState（访问其他 Store 的 state）

---

## 4. 使用 Store

在组件中使用 Store，需要三步：

### 4.1 导入 Store

```javascript
# 相对路径（推荐）
import { useUserStore } from '@/stores/user'

# 或使用绝对路径（需配置 alias）
import { useUserStore } from '/stores/user'
```

### 4.2 调用 Store 函数

```javascript
const userStore = useUserStore()   # ⚠️ 注意：是调用函数，不是 new！
```

**重要**：Store 通过函数调用获取实例，**不能用 new 关键字**。每次调用 `useUserStore()` 会返回同一个实例（Pinia 保证单例）。

### 4.3 访问 state 和调用 actions

```javascript
# 读取 state
userStore.token
userStore.userInfo?.name

# 调用 actions
await userStore.login('admin', '123456')
userStore.logout()
```

**在模板中使用**：

```vue
<template>
  <div>
    # 直接访问 state
    欢迎，{{ userStore.userInfo?.name || '用户' }}
    
    # 调用 actions（需在 <script> 中）
    <el-button @click="userStore.loadDepartments">加载部门</el-button>
    
    # 访问 getters
    部门数：{{ userStore.departmentCount }}
  </div>
</template>
```

---

## 5. 多 Store 架构

项目按职责划分多个 Store，每个 Store 管理相关状态。

### 5.1 user Store

`src/stores/user.js` 管理用户相关的状态：

```javascript
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { loginApi } from '@/api/auth'
import { deptApi } from '@/api/dept'
import { empApi } from '@/api/emp'

export const useUserStore = defineStore('user', {
  state: () => ({
    userInfo: null,
    token: localStorage.getItem('token') || null,
    departments: [],   # 部门列表
    allEmployees: []    # 全部员工列表（用于下拉选择）
  }),

  actions: {
    # 登录
    async login(username, password) {
      const result = await loginApi.login({ username, password })
      this.token = result.token
      this.userInfo = {
        id: result.id,
        username: result.username,
        name: result.name
      }
      localStorage.setItem('token', this.token)
      localStorage.setItem('userInfo', JSON.stringify(this.userInfo))
      return this.userInfo
    },

    # 登出
    logout() {
      this.token = null
      this.userInfo = null
      localStorage.removeItem('token')
      localStorage.removeItem('userInfo')
    },

    # 初始化加载
    async loadDepartments() {
      this.departments = await deptApi.list()
    },
    async loadAllEmployees() {
      this.allEmployees = await empApi.listAll()
    }
  }
})
```

### 5.2 employee Store

`src/stores/employee.js` 管理员工列表相关的状态（分页、增删改）：

```javascript
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { empApi } from '@/api/emp'

export const useEmployeeStore = defineStore('employee', {
  state: () => ({
    employees: [],            # 当前页员工列表
    pageInfo: {               # 分页信息
      total: 0,               # 总条数
      currentPage: 1,         # 当前页码
      pageSize: 10,           # 每页条数
      searchParams: {}        # 搜索条件
    }
  }),

  actions: {
    # 分页查询员工
    async list(params = {}) {
      const result = await empApi.list(params)  # 调用 API
      this.employees = result.rows              # 更新列表
      this.pageInfo.total = result.total        # 更新总条数
      this.pageInfo.currentPage = params.page || 1
      this.pageInfo.pageSize = params.pageSize || 10
      this.pageInfo.searchParams = params       # 保存搜索条件
    },

    # 添加员工（添加后刷新列表）
    async add(data) {
      await empApi.add(data)
      await this.list(this.pageInfo.searchParams)   # 重新加载
    },

    # 更新员工（更新后刷新列表）
    async update(data) {
      await empApi.update(data)
      await this.list(this.pageInfo.searchParams)
    },

    # 批量删除（删除后刷新列表）
    async delete(ids) {
      await empApi.delete(ids)
      await this.list(this.pageInfo.searchParams)
    },

    # 获取单个员工详情
    async getById(id) {
      return await empApi.getById(id)
    }
  }
})
```

### 5.3 Store 协作

不同 Store 之间可以相互协作，例如 user Store 初始化时调用 employee Store：

```javascript
# stores/user.js
import { useEmployeeStore } from '@/stores/employee'

export const useUserStore = defineStore('user', {
  state: () => ({ /* ... */ }),
  
  actions: {
    async loadInitialData() {
      # 同时加载多个数据
      await Promise.all([
        this.loadDepartments(),
        this.loadAllEmployees(),
        useEmployeeStore().list()    # 获取其他 Store 实例
      ])
    }
  }
})
```

---

## 6. 状态持久化

默认情况下，Pinia Store 的状态只在内存中，页面刷新后丢失。需要持久化时：

### 6.1 手动持久化（本项目做法）

在 actions 中手动操作 localStorage：

```javascript
actions: {
  async login(username, password) {
    const result = await loginApi.login({ username, password })
    this.token = result.token
    
    # 持久化到 localStorage
    localStorage.setItem('token', this.token)
    localStorage.setItem('userInfo', JSON.stringify(this.userInfo))
  },

  # 初始化时从 localStorage 恢复
  init() {
    this.token = localStorage.getItem('token') || null
    const userInfo = localStorage.getItem('userInfo')
    this.userInfo = userInfo ? JSON.parse(userInfo) : null
  }
}
```

### 6.2 使用 pinia-plugin-persistedstate（推荐）

一个流行的 Pinia 插件，自动持久化状态：

```bash
npm install pinia-plugin-persistedstate
```

```javascript
# src/stores/index.js (集中创建 Store)
import { createPinia } from 'pinia'
import persist from 'pinia-plugin-persistedstate'

const pinia = createPinia()
pinia.use(persist)   # 启用持久化插件

# 在 defineStore 中配置 persist
export const useUserStore = defineStore('user', {
  state: () => ({ /* ... */ }),
  persist: {
    key: 'user-store',        # localStorage 的 key
    paths: ['token', 'userInfo'],   # 需要持久化的 state 路径
    storage: localStorage     # 存储方式（localStorage/sessionStorage）
  }
})
```

**插件优势**：
- 自动同步 localStorage 和 state
- 页面刷新后自动恢复状态
- 无需手动写 localStorage 代码

---

## 7. Store 与 API 的配合

Store 是调用 API 的最佳位置，因为：

1. **集中管理**：API 调用集中在 Store，组件只需调用 Store 方法
2. **代码复用**：多个页面共享同一个 Store，无需重复写 API 逻辑
3. **状态更新**：API 调用后可以自动更新 state，触发组件重新渲染

**在组件中的使用模式**：

```vue
# 不推荐：组件直接调用 API
<script setup>
import { deptApi } from '@/api/dept'
const departments = ref([])

async function load() {
  departments.value = await deptApi.list()
}
</script>

# 推荐：通过 Store 调用 API
<script setup>
import { useUserStore } from '@/stores/user'
const userStore = useUserStore()

async function load() {
  await userStore.loadDepartments()   # Store 内部处理 API
}
</script>
```

---

## 8. Store 与路由的配合

Store 和路由配合使用，实现完整的页面功能：

### 8.1 路由守卫中使用 Store

在路由守卫中检查用户信息（从 Store 获取）：

```javascript
# router/index.js
import { useUserStore } from '@/stores/user'

router.beforeEach(async (to, from, next) => {
  const userStore = useUserStore()
  
  # 确保 Store 已初始化（从 localStorage 恢复）
  await userStore.init()
  
  const isAuthenticated = !!userStore.token
  
  if (to.path !== '/login' && !isAuthenticated) {
    next('/login')
  } else {
    next()
  }
})
```

### 8.2 页面初始化数据

在 Layout 组件的 `onMounted` 中初始化 Store 数据：

```vue
<!-- views/layout/Layout.vue -->
<script setup>
import { onMounted } from 'vue'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()

onMounted(async () => {
  try {
    await userStore.loadDepartments()
    await userStore.loadAllEmployees()
  } catch (error) {
    console.warn('初始化数据失败', error)
  }
})
</script>
```

### 8.3 登出逻辑

登出时同时清除 Store 和 localStorage：

```javascript
async function logout() {
  await ElMessageBox.confirm('确定退出？', '提示')
  await userStore.logout()   # 清除 state 和 localStorage
  router.push('/login')      # 跳转登录页
}
```

---

## 9. 完整 Store 示例

下面是 Tlias 管理系统的完整 Store 实现（合并展示）：

```javascript
// src/stores/user.js
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { loginApi } from '@/api/auth'
import { deptApi } from '@/api/dept'
import { empApi } from '@/api/emp'

export const useUserStore = defineStore('user', {
  state: () => ({
    userInfo: null,
    token: localStorage.getItem('token') || null,
    departments: [],
    allEmployees: []
  }),

  actions: {
    async login(username, password) {
      const result = await loginApi.login({ username, password })
      this.token = result.token
      this.userInfo = {
        id: result.id,
        username: result.username,
        name: result.name
      }
      localStorage.setItem('token', this.token)
      localStorage.setItem('userInfo', JSON.stringify(this.userInfo))
      return this.userInfo
    },

    logout() {
      this.token = null
      this.userInfo = null
      localStorage.removeItem('token')
      localStorage.removeItem('userInfo')
    },

    async loadDepartments() {
      this.departments = await deptApi.list()
    },

    async loadAllEmployees() {
      this.allEmployees = await empApi.listAll()
    }
  }
})

# 导出 employee Store
export const useEmployeeStore = defineStore('employee', {
  state: () => ({
    employees: [],
    pageInfo: {
      total: 0,
      currentPage: 1,
      pageSize: 10,
      searchParams: {}
    }
  }),

  actions: {
    async list(params = {}) {
      const result = await empApi.list(params)
      this.employees = result.rows
      this.pageInfo.total = result.total
      this.pageInfo.currentPage = params.page || 1
      this.pageInfo.pageSize = params.pageSize || 10
      this.pageInfo.searchParams = params
    },

    async add(data) {
      await empApi.add(data)
      await this.list(this.pageInfo.searchParams)
    },

    async update(data) {
      await empApi.update(data)
      await this.list(this.pageInfo.searchParams)
    },

    async delete(ids) {
      await empApi.delete(ids)
      await this.list(this.pageInfo.searchParams)
    },

    async getById(id) {
      return await empApi.getById(id)
    }
  }
})
```

---

## 10. 常见用法总结

### 10.1 基础操作

```javascript
# 创建 Store
const store = useUserStore()

# 读取 state
store.token
store.userInfo?.name

# 修改 state（在 actions 中）
this.token = 'new-token'

# 调用异步 actions
await store.login('admin', '123456')

# 访问 getters
store.departmentCount
```

### 10.2 在 Store 中获取其他 Store

```javascript
import { useEmployeeStore } from '@/stores/employee'

export const useUserStore = defineStore('user', {
  actions: {
    async someMethod() {
      const employeeStore = useEmployeeStore()   # 获取其他 Store 实例
      await employeeStore.list()
    }
  }
})
```

### 10.3 在 Store 中调用 API

```javascript
actions: {
  async fetchData() {
    # 直接调用 API（已封装在 api/ 目录）
    const result = await deptApi.list()
    this.data = result
  }
}
```

### 10.4 响应式追踪

Pinia 的 state 是响应式的，当 state 变化时，所有用到该 state 的组件都会自动更新：

```vue
<template>
  # 只要 userStore.token 变化，这个模板会自动更新
  {{ userStore.token ? '已登录' : '未登录' }}
</template>
```

---

## 小结

Pinia 是 Vue3 官方推荐的状态管理库，核心要点：

1. **定义 Store**：`defineStore('id', { state, actions, getters })`
2. **state**：必须是函数返回的响应式对象
3. **actions**：业务方法，可异步，可修改 this.state
4. **getters**：计算属性，基于 state 衍生
5. **使用 Store**：`useStore()` 调用，不是 new
6. **持久化**：手动 localStorage 或 pinia-plugin-persistedstate 插件
7. **多 Store**：按职责拆分，user Store + employee Store

掌握 Pinia 的使用方法，就可以高效地管理 Vue3 应用的全局状态，实现组件间的数据共享。

关联知识点：
- [[API接口封装]] API 调用
- [[Vue Router路由配置]] 路由与状态配合
- [[学习/JavaWeb/知识条目/前端/script setup语法]] 组件中使用 Store
- [[响应式对象]] 响应式原理