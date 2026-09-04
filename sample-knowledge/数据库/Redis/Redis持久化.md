---
type: 学习笔记
title: Redis持久化
created: 2026-08-14
updated: 2026-08-14
subject: 数据库
tags: [Redis, 持久化, RDB, AOF]
---

> Redis 数据常驻内存，断电即失。持久化是保障数据安全的核心机制。本文深入讲解 RDB、AOF 及混合持久化的原理、配置、优劣势与生产选型。

## 目录

- [1. 为什么需要持久化](#1-为什么需要持久化)
- [2. RDB（快照持久化）](#2-rdb快照持久化)
- [3. AOF（追加日志持久化）](#3-aof追加日志持久化)
- [4. 混合持久化](#4-混合持久化)
- [5. RDB vs AOF 深度对比](#5-rdb-vs-aof-深度对比)
- [6. 生产环境配置方案](#6-生产环境配置方案)
- [7. 故障恢复与数据一致性](#7-故障恢复与数据一致性)
- [小结](#小结)

---

> 相关笔记：[[Redis概述]] · [[Redis事务]]

---

## 1. 为什么需要持久化

### 1.1 Redis 的内存特性

```
┌─────────────────────────────────────┐
│           Redis 内存数据区            │
│  ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐  │
│  │ key │ │ key │ │ key │ │ key │  │  ← 进程内，速度极快
│  │ val │ │ val │ │ val │ │ val │  │
│  └─────┘ └─────┘ └─────┘ └─────┘  │
└─────────────────────────────────────┘
         ↑
    断电 / 重启 / 崩溃 → 数据全部丢失
```

Redis 追求极致性能，所有数据放在内存中。**内存不是持久存储**，必须借助持久化机制将数据写到磁盘。

### 1.2 持久化的核心目标

| 目标 | 含义 |
|------|------|
| **数据不丢** | 服务重启后尽可能恢复所有数据 |
| **恢复快速** | 重启加载数据的时间可接受 |
| **影响小** | 持久化过程不能显著拖慢正常服务 |
| **文件可控** | 持久化文件体积 manageable |

---

## 2. RDB（快照持久化）

### 2.1 工作原理

```
主进程                          子进程
  │                              │
  ├── BGSAVE ──────────────────→ │ fork() 创建子进程
  │   （主进程继续处理请求）        │
  │                              ├── 子进程独立读取内存数据
  │                              ├── 写入临时 RDB 文件
  │                              ├── 完成后原子替换 dump.rdb
  │                              │
  │ ←──── EXECUTE 完成通知 ──────│
```

关键点：
- `fork()` 创建子进程，**子进程共享主进程的内存页**（写时复制 COW）
- 主进程继续响应命令，不受阻塞
- 子进程负责将内存数据序列化为 RDB 二进制文件

### 2.2 RDB 文件特点

```bash
# 默认文件名和路径
dump.rdb        # 文件名
dir ./          # 存放路径（由 dir 配置项决定）
```

RDB 文件是**压缩后的二进制格式**，体积小、传输快、适合备份和灾备。

### 2.3 触发方式

#### 自动触发（配置文件）

```bash
# save <秒数> <变更次数>
# 含义：在指定秒数内，至少有指定次数的写操作，则触发快照
save 900 1      # 900秒内至少1次写 → 快照
save 300 10     # 300秒内至少10次写 → 快照
save 60  10000  # 60秒内至少10000次写 → 快照

# 全部注释掉则禁用自动触发
# save ""
```

#### 手动触发

```bash
BGSAVE        # 异步 fork 子进程写 RDB（推荐，不阻塞主线程）
SAVE          # 同步写 RDB（阻塞主线程，生产严禁使用）
```

```
SAVE vs BGSAVE：
SAVE：在主进程中直接写文件 → 阻塞所有请求 → 高延迟飙升 → 生产禁止
BGSAVE：fork 子进程写文件 → 主进程继续服务 → 推荐
```

### 2.4 RDB 重写（手动压缩）

```bash
# 当 RDB 文件过大时，手动触发压缩
BGREWRITEAOF        # 注意：这个命令是 AOF 重写，不是 RDB！
```

> RDB 本身没有"重写"概念，它每次都是全量快照。文件膨胀主要来自频繁写入。控制手段是调整 save 规则或减少写频率。

### 2.5 RDB 优缺点

| 优点 | 缺点 |
|------|------|
| 文件紧凑，压缩后体积小 | **无法做到实时持久化**，两次快照之间数据可能丢失 |
| 恢复速度快（单次加载） | **fork 大内存时耗时较长**，可能引起延迟尖刺 |
| 适合灾难恢复和备份 | **COW 内存膨胀**：fork 后主进程写新数据会触发页面复制 |
| 对主进程性能影响小 | 不能代替 AOF 做细粒度恢复 |

---

## 3. AOF（追加日志持久化）

### 3.1 工作原理

AOF 记录**每一个写命令**，以日志形式追加到文件。重启时重放日志重建数据。

```
写命令流入 → 写入 OS 缓冲区 → 按策略刷盘 → aof文件追加
```

### 3.2 核心配置

```bash
# 开启 AOF
appendonly yes

# 刷盘策略（最重要）
appendfsync always        # 每次写命令立即刷盘 → 最安全，性能最差
appendfsync everysec      # 每秒刷盘一次 → 默认推荐，平衡性能与安全
appendfsync no            # 由 OS 决定刷盘时机 → 最快，数据风险最高
```

### 3.3 三种刷盘策略详解

```
时间轴：写命令到达 → 写入缓冲区 → 刷盘到磁盘
         │              │            │
always   ├──────┬───────┤            ├── 每次写都 fsync → 最多丢 0 条，IO 压力极大
everysec ├──────┼───────┼────────────┤  每秒 fsync 一次 → 最多丢 1 秒数据，推荐
no       ├──────┼───────┼────────────┤  OS 控制刷盘（通常30秒）→ 可能丢大量数据
         命令1   命令2   命令3   命令4   命令N
              ↑ 每秒定时 fsync
```

| 策略 | 安全性 | 性能 | 适用场景 |
|------|--------|------|---------|
| `always` | ⭐⭐⭐ 最高 | ⭐ 最低 | 金融级要求，极少用 |
| `everysec` | ⭐⭐ 高 | ⭐⭐⭐ 高 | **生产环境默认推荐** |
| `no` | ⭐ 低 | ⭐⭐⭐⭐ 最高 | 可接受数据丢失的缓存场景 |

### 3.4 AOF 重写（Compaction）

AOF 文件随着时间增长会越来越大，需要定期**重写**——用当前数据状态生成新的精简日志，而不是保留所有历史命令。

```
旧 AOF 文件：
  SET name zhangsan
  SET age 28
  SET name lisi        ← 覆盖前面的 SET name
  HSET user:1 name zhangsan
  HSET user:1 age 28
  HDEL user:1 age      ← 最后 age 被删了

重写后 AOF 文件：
  HSET user:1 name zhangsan   ← 只保留最终状态对应的命令
```

**触发方式：**

```bash
# 手动触发
BGREWRITEAOF

# 自动触发（满足条件时由 Redis 自动执行）
auto-aof-rewrite-percentage 100   # AOF 文件比上次重写后增长超过 100% 时触发
auto-aof-rewrite-min-size 64mb    # AOF 文件至少 64MB 才允许重写
```

### 3.5 AOF 重写流程

```
主进程                         子进程
  │                              │
  ├── BGREWRITEAOF ───────────→ │ fork() 创建子进程
  │                              │
  │   （主进程继续处理请求）        │
  │                              ├── 读取当前内存数据
  │                              ├── 生成精简的 AOF 重写文件（.tmp）
  │                              │
  │                              │  开始重写时：
  │                              │  ① 子进程写 .tmp 文件
  │                              │  ② 主进程将期间新写的命令
  │                              │    同时写入"重写缓存"
  │                              │
  │                              ├── .tmp 文件完成后
  │                              │  将重写缓存中的命令追加进去
  │                              │
  │                              ├── 原子替换 aof.tmp → aof.aof
  │                              │
  │ ←──── REWRITE 完成通知 ──────│
  │   （主进程继续响应）
```

### 3.6 AOF 优缺点

| 优点 | 缺点 |
|------|------|
| **数据安全性高**，everysec 策略最多丢 1 秒数据 | **文件体积大**，即使重写后通常仍大于 RDB |
| 每条写命令都有日志，可精确恢复 | **恢复速度慢**，命令多时重放耗时较长 |
| 重写过程对主进程影响小 | `everysec` 策略在系统崩溃时可能丢失最后一条不完整命令 |
| 可通过 `no-appendfsync-on-rewrite yes` 在重写期间暂停刷盘，避免 IO 争抢 | `appendfsync always` 性能影响极大，几乎不可用于生产 |

---

## 4. 混合持久化

### 4.1 什么是混合持久化

Redis 4.0+ 引入，将 RDB 快照 + AOF 日志结合起来：
- **AOF 重写时**，先写一段 RDB 格式的全量数据（快速加载部分）
- **再追加 AOF 增量日志**（保证最近数据的精确性）

```
aof.aof 文件格式：
┌────────────────────────┬─────────────────────────────┐
│   RDB 格式的快照数据      │   AOF 追加的写命令日志         │
│ （快速加载全量数据）        │ （精确恢复最近变更）             │
└────────────────────────┴─────────────────────────────┘
      恢复时：先加载 RDB 部分（快）→ 再重放 AOF 部分（准）
```

### 4.2 配置

```bash
# 开启混合持久化（Redis 4.0+）
aof-use-rdb-preamble yes

# 配合 AOF 配置
appendonly yes
appendfsync everysec
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb
```

### 4.3 优势

| 对比维度 | AOF alone | 混合持久化 |
|---------|-----------|-----------|
| 恢复速度 | 慢（全量重放命令） | 快（RDB 部分快速加载） |
| 数据丢失量 | 最多 1 秒 | 最多 1 秒 |
| AOF 文件大小 | 大 | 较小（RDB 部分压缩了历史数据） |
| 重启时 IO 压力 | 高 | 低（RDB 部分一次性加载） |

---

## 5. RDB vs AOF 深度对比

### 5.1 全面对比表

| 维度 | RDB | AOF |
|------|-----|-----|
| 数据安全性 | 低（两次快照间数据可能丢失） | 高（everysec 最多丢 1 秒） |
| 恢复速度 | 快（单文件加载） | 慢（命令逐条重放） |
| 文件大小 | 小（压缩二进制） | 大（文本日志，即使重写） |
| 对主进程影响 | fork 时短暂延迟，COW 内存增长 | 重写时 fork + IO 压力 |
| 适用场景 | 灾难恢复、数据备份 | 数据高可靠要求、细粒度恢复 |
| 数据丢失窗口 | 取决于 save 规则（分钟级） | 取决于 appendfsync（秒级/实时） |

### 5.2 数据丢失窗口可视化

```
时间线（假设 save 900 1 配置）：
  T0  ── T300 ── T600 ── T900 ── T1200 ── T1500
  │      │      │      │      │      │      │
  ●      ●      ●      ●      ●      ●      ●     ← RDB 快照点
  └──── 丢失 ~900秒数据 ────┘

AOF everysec：
  T0 ── T1 ── T2 ── T3 ── T4 ── T5 ── T6
  ●────●────●────●────●────●────●     ← 每秒刷盘
  └─ 最多丢失 1 秒数据 ─┘
```

---

## 6. 生产环境配置方案

### 6.1 方案一：RDB only（缓存型场景）

适用：纯缓存场景，允许少量数据丢失，重视恢复速度。

```bash
# redis.conf
# 关闭 AOF
appendonly no

# RDB 配置
save 900 1
save 300 10
save 60 10000
rdbcompression yes
rdbchecksum yes
dbfilename dump.rdb
```

### 6.2 方案二：AOF only（数据敏感场景）

适用：不允许丢失数据，如用户积分、订单状态等。

```bash
# redis.conf
appendonly yes
appendfsync everysec       # 推荐：平衡性能与安全
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb
no-appendfsync-on-rewrite yes  # 重写期间暂停刷盘，避免 IO 争抢
```

### 6.3 方案三：混合持久化（生产推荐 ⭐）

适用：绝大多数生产场景，兼顾数据安全与恢复速度。

```bash
# redis.conf
appendonly yes
appendfsync everysec
aof-use-rdb-preamble yes       # 开启混合持久化
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb
no-appendfsync-on-rewrite yes

# RDB 作为备用（混合模式下 RDB 文件也会在重写时生成）
save 900 1
save 300 10
save 60 10000
```

### 6.4 完整生产配置模板

```bash
# ========== 基础配置 ==========
bind 0.0.0.0
port 6379
daemonize no
pidfile /var/run/redis_6379.pid

# ========== 内存管理 ==========
maxmemory 4gb
maxmemory-policy allkeys-lru    # 内存满时驱逐策略

# ========== 持久化：混合模式（推荐）==========
appendonly yes
appendfsync everysec
aof-use-rdb-preamble yes
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb
no-appendfsync-on-rewrite yes

save 900 1
save 300 10
save 60 10000

# ========== 安全 ==========
requirepass your_strong_password
rename-command FLUSHDB ""       # 禁用危险命令
rename-command FLUSHALL ""
rename-command CONFIG "CONFIG_b4f0c2"   # 限制 CONFIG 访问

# ========== 日志 ==========
loglevel notice
logfile /var/log/redis/redis.log
```

---

## 7. 故障恢复与数据一致性

### 7.1 正常重启恢复顺序

```
Redis 启动时：
  1. 检查是否有 AOF 文件
     ├─ 有 → 加载 AOF 文件（含 RDB preamble 部分优先）
     └─ 无 → 检查 RDB 文件
         ├─ 有 → 加载 RDB 文件
         └─ 无 → 空实例启动
```

```bash
# 恢复优先级：AOF > RDB
# 如果同时存在，Redis 优先用 AOF 恢复（因为 AOF 更完整）
```

### 7.2 异常宕机后的数据状态

```
情况一：appendfsync everysec + 系统崩溃
  → 可能丢失最后 1 秒内的写命令
  → 已刷盘的命令一定在 AOF 文件中

情况二：appendfsync no + 系统崩溃
  → 可能丢失 OS 缓冲区中未刷盘的所有数据
  → 风险极高，生产禁止

情况三：BGSAVE 进行中崩溃
  → RDB 文件可能不完整，Redis 会自动删除损坏的 RDB
  → fallback 到 AOF 或空实例
```

### 7.3 手动恢复流程

```bash
# 步骤一：停止 Redis
redis-cli -a password SHUTDOWN NOSAVE   # 不保存，直接停止

# 步骤二：备份当前数据文件
cp -r /data/redis /data/redis.backup.$(date +%Y%m%d)

# 步骤三：检查 AOF 文件完整性
redis-check-aof /data/redis/aof.aof
# 输出：AOF analyzed: size=xxx, ok_up_to=yyy, ok=1 → 完整
# 输出：ok=0 → 文件损坏，需要截断

# 步骤四：修复损坏的 AOF（可选）
redis-check-aof --fix /data/redis/aof.aof
# 会提示截断损坏部分，确认后恢复

# 步骤五：启动 Redis
redis-server /etc/redis/redis.conf
```

### 7.4 AOF 文件损坏处理

```bash
# 检查并修复（会提示将要截断的字节数）
redis-check-aof --fix /data/redis/aof.aof

# 交互式修复过程：
# Current AOF error. Size: 1234567. Last OK command: SET shop:stock:9527 99
# Fix this AOF file? (y/N): y
# Truncating AOF file to 1234500 bytes

# 修复后重新启动
redis-server /etc/redis/redis.conf
```

---

## 小结

- **RDB**：快照式持久化，文件小恢复快，但数据丢失窗口大（分钟级），适合备份和灾备
- **AOF**：命令日志式持久化，数据安全高（everysec 最多丢 1 秒），但文件大恢复慢
- **混合持久化**（RDB preamble + AOF 增量）是生产推荐方案，兼顾恢复速度和安全性
- **刷盘策略**：生产用 `everysec`，不要设 `always`（性能太差）也不要设 `no`（风险太高）
- **AOF 重写**是必要的维护操作，Redis 自动触发也可手动 `BGREWRITEAOF`
- 恢复时优先使用 AOF，损坏的 AOF 文件可用 `redis-check-aof --fix` 修复

<!-- KB:ANNOTATIONS -->
