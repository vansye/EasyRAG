---
type: 学习笔记
title: Redis过期Key处理
created: 2026-08-14
updated: 2026-08-14
subject: 数据库
tags: [Redis, 过期, TTL, 删除]
---

> Redis 的 Key 可以设置过期时间，但「过期」不等于「立即删除」。本文深入讲解过期机制的底层原理、删除策略、内存回收以及常见陷阱。

## 目录

- [1. 过期时间设置命令](#1-过期时间设置命令)
- [2. 过期处理的三种策略](#2-过期处理的三种策略)
- [3. 惰性删除详解](#3-惰性删除详解)
- [4. 定期删除详解](#4-定期删除详解)
- [5. 两种策略的配合机制](#5-两种策略的配合机制)
- [6. 过期 Key 的内存回收](#6-过期-key-的内存回收)
- [7. 常见问题与陷阱](#7-常见问题与陷阱)
- [小结](#小结)

---

> 相关笔记：[[Redis内存淘汰机制]] · [[Redis持久化]]

---

## 1. 过期时间设置命令

### 1.1 设置过期时间的命令

```bash
EXPIRE key seconds        # 设置过期时间（秒）
PEXPIRE key milliseconds  # 设置过期时间（毫秒，Redis 2.6+）
EXAT key timestamp        # 设置绝对时间戳过期（秒，Redis 7.0+）
PEXAT key timestamp_ms    # 设置绝对时间戳过期（毫秒，Redis 7.0+）
SET key value EX seconds  # 写入时直接设过期（推荐，原子操作）
SET key value PX milliseconds
```

```bash
# 查看剩余过期时间
TTL key       # 返回剩余秒数，-1 表示永不过期，-2 表示 key 不存在
PTTL key      # 毫秒精度版

# 取消过期时间
PERSIST key   # 将有过期时间的 key 转为永久 key
```

### 1.2 过期时间粒度

```bash
# EXPIRE 精度只有秒，PEXPIRE 精度到毫秒
EXPIRE key 1    # 1 秒后过期（实际可能在 0~1 秒之间偏移）
PEXPIRE key 500 # 500 毫秒后过期（精确到毫秒）

# Redis 内部统一以毫秒存储过期时间
# TTL 返回时换算成秒（向下取整）
```

### 1.3 过期时间不可靠的场景

```bash
# 场景一：EXPIRE 执行前 key 已不存在
EXPIRE nonexistent_key 60    # (integer) 0  → 设置失败
EXPIRE mykey 60              # 如果 mykey 不存在，同样返回 0

# 场景二：设置过期后，key 被 DEL 删除
SET mykey "hello" EX 60      # OK
DEL mykey                    # key 已删除，过期时间自然消失
EXPIRE mykey 60              # 0  → 重新设置也返回 0（key 不存在）

# 场景三：OVERWRITE 子命令（Redis 7.0+）
# 即使 key 已有过期时间，也可以覆盖
EXPIRE key 60 OVERWRITE      # 覆盖原有过期时间
EXPIRE key 60 NX             # 仅当 key 没有过期时间时才设置
```

---

## 2. 过期处理的三种策略

> **核心认知**：Redis 不会在 key 过期的那一刻立即删除它。三种策略协同工作，确保过期 key 最终被清理。

```
策略一：惰性删除（Lazy Expiration）
  → key 被访问时检查是否过期，过期则删

策略二：定期删除（Active Expiration）
  → Redis 后台定时抽样检查，批量删除过期 key

策略三：内存淘汰（Eviction）
  → 内存满时，按策略淘汰 key（含过期 key 和普通 key）
  → 见 [[Redis内存淘汰机制]]
```

本章重点讲解**策略一和策略二**。

---

## 3. 惰性删除详解

### 3.1 工作原理

**每次访问一个 key 时**，Redis 都会检查该 key 是否已过期，如果过期则立即删除。

```
客户端请求 GET key
        ↓
Redis 检查 key 的过期时间
        ↓
    ┌─── key 已过期？───┐
    │                   │
   是                   否
    │                   │
    ↓                   ↓
  删除 key          正常返回 value
  返回 null
```

### 3.2 哪些操作会触发惰性删除

| 操作类型 | 触发命令 |
|---------|---------|
| 读操作 | `GET` / `HGET` / `LINDEX` / `SMEMBERS` / `ZRANGE` / ... |
| 写操作 | `SET` / `HSET` / `LPUSH` / `SADD` / `ZADD` / ... |
| 元数据操作 | `TTL` / `PTTL` / `EXPIRE` / `DEL` / `MOVE` / ... |
| 统计操作 | `SCARD` / `ZCARD` / `LLEN` |

> **几乎所有涉及具体 key 的命令都会触发惰性删除检查**。

### 3.3 惰性删除的缺陷

```
问题：惰性删除只在 key 被访问时才触发。

如果大量 key 过期后不再被访问：
  → 这些 key 一直占用内存，不会被清理
  → 造成内存泄漏（"僵尸 key"）

示例：
  100 万个 key，全部设置了 1 秒的 TTL
  1 秒后，这 100 万个 key 全部过期
  但如果业务不再访问这些 key → 惰性删除永远不触发它们
  → 内存一直被占用直到内存满触发淘汰，或者定期删除介入
```

---

## 4. 定期删除详解

### 4.1 工作原理

Redis 后台**周期性**地从设置了过期时间的 key 中**随机抽样**，检查并删除过期 key。

```
每 hz 次/秒（默认 10 次）触发一次定期检查：
  ├── 从带 TTL 的 key 空间中随机抽取 N 个
  ├── 检查每个 key 是否过期
  ├── 过期 → 删除，统计计数
  ├── 未过期 → 跳过
  └── 如果本轮删除比例超过阈值 → 继续下一轮抽查
```

### 4.2 内部实现细节

```c
// Redis 源码简化逻辑
int activeExpireCycle(int type) {
    // type: ACTIVE_EXPIRE_CYCLE_FAST 或 SLOW
    for (int j = 0; j < dbs_per_call; j++) {
        int expired = 0;
        redisDb *db = server.db + j;

        // 遍历这批数据库中随机抽样的 key
        for (int i = 0; i < samples; i++) {
            dictEntry *de = dictGetRandomKey(db->expires);
            if (!de) break;

            // 检查是否过期
            if (activeExpireTryExpire(db, de)) {
                expired++;
            }
        }

        // 如果过期键比例过高，继续下一轮（防止单轮扫描时间过长）
        work_done += expired;
        if (expired > threshold) continue_cycle = 1;
    }
    return work_done;
}
```

### 4.3 可调参数

```bash
# redis.conf

# 每秒执行次数（控制定期检查的频率）
hz 10                    # 默认 10，范围 1~500
# 高负载场景可升至 100，增加过期键清理速度

# 每次检查扫描的数据库数量
active-expire-cycle-tries 3   # 默认 3，尝试轮询的次数

# 快速模式 vs 慢速模式的时间预算（毫秒）
# 快速模式：每次最多 1ms
# 慢速模式：每次最多 10ms（Redis 5.0+）
# 由 Redis 自动控制，无需手动配置
```

### 4.4 定期删除的缺陷

```
问题一：抽样是随机的，可能漏掉大量过期 key
  → 某些过期 key 连续多轮都没有被抽中
  → 这些 key 持续占用内存

问题二：扫描开销
  → 高频抽样会增加 CPU 开销
  → 尤其是在 key 数量巨大的场景
```

---

## 5. 两种策略的配合机制

### 5.1 协同工作原理

```
                    ┌─────────────────────┐
                    │    过期 key 集合      │
                    │                     │
Key 被访问           │  ················   │  定期抽查
  ↓                 │      ↑    ↑         │    ↓
惰性删除检查 ──────→│      │    │         │──→ 定期删除
  ↓                 │      │    │         │    ↓
过期？→ DEL        │      │    │ 未过期   │ 过期？→ DEL
未过期→ 返回        │      │    │         │ 未过期→ 跳过
                    └─────────────────────┘

惰性删除：精确但被动，只清理被访问的过期 key
定期删除：粗略但主动，清理未被访问的过期 key
两者互补：确保所有过期 key 最终都被清除
```

### 5.2 工作机制对比

| 维度 | 惰性删除 | 定期删除 |
|------|---------|---------|
| 触发时机 | key 被访问时 | 后台定时抽查 |
| 精确度 | 100%（访问即检查） | 随机抽样，有遗漏风险 |
| CPU 开销 | 低（访问时顺带检查） | 中（定期扫描开销） |
| 内存回收及时性 | 取决于访问频率 | 相对稳定 |
| 责任范围 | 清理被访问的过期 key | 清理未被访问的过期 key |

### 5.3 完整的内存回收流程

```
场景：100 万个 key 全部设置 1 秒 TTL，之后不再访问

时间线：
  T=0s    ：100 万个 key 写入，全部 TTL=1s
  T=1s    ：所有 key 过期
  T=1s~5s ：
    - 惰性删除：无访问 → 不触发 → 100 万 key 仍在内存
    - 定期删除：每秒抽查 → 逐步清理（假设每秒清理 10 万）
  T=5s~10s：定期删除持续工作，逐步清完
  T=10s+  ：内存释放，恢复常态
```

---

## 6. 过期 Key 的内存回收

### 6.1 删除后的内存管理

```
key 被删除
    ↓
释放 key 的字典节点（dictEntry）
    ↓
释放 key 的字符串对象（sds）
    ↓
释放 value 的字符串对象
    ↓
释放 value 的结构（hash/list/set/zset 等）
    ↓
内存归还到 Redis 的内存池（jemalloc）
```

### 6.2 内存池 vs OS 内存

```
Redis 使用 jemalloc 作为内存分配器：

Redis 进程 → jemalloc 内存池（从 OS 申请的大块内存）
                    ↓
              各个 key/value 对象

key 被删除后：
  → 内存归还到 jemalloc 的 free list
  → 不会立即归还给操作系统
  → 下次分配时复用
```

> **影响**：key 删除后，`used_memory` 会下降，但 `used_memoryrss`（RSS，实际物理内存）可能不会立即下降。这是正常现象，不是内存泄漏。

### 6.3 内存碎片问题

```
长时间运行后，频繁增删 key 会导致内存碎片：

  free space    key1    free    key2    free    key3
  ████████    ████    ███    ████    ████    ████

jemalloc 的 free list 中积累了很多小碎片，
导致 used_memory 不高，但 used_memoryrss 很高。
```

**监控碎片率：**

```bash
INFO memory | grep mem_fragmentation_ratio
# 1.0 ~ 1.5  → 正常
# > 1.5       → 碎片较严重，考虑重启或升级 Redis
# < 1.0       → Redis 正在从 OS 回收内存（正常）
```

---

## 7. 常见问题与陷阱

### 7.1 陷阱一：过期 ≠ 立即删除

```bash
# 很多人误以为 EXPIRE 1 后，1 秒整准时 key 就消失了
SET mykey "value" EX 1    # 设置 1 秒后过期
# 1 秒后：
GET mykey                  # 可能仍然返回 "value"！
                            # 因为没触发惰性删除，定期删除也还没抽到
# 再等一会：
GET mykey                  # 这才可能返回 nil
```

> **关键认知**：过期时间只是一个"标记"，key 的实际删除取决于惰性删除和定期删除的执行时机。

### 7.2 陷阱二：SCAN 不会返回已过期但未删除的 key

```bash
# SCAN 遍历时，会自动跳过已过期（但未物理删除）的 key
SCAN 0 MATCH mypattern COUNT 100
# → 结果中不会出现已过期但尚未被惰性/定期删除的 key
```

### 7.3 陷阱三：批量过期导致性能抖动

```
场景：某个 key 是热点，有大量客户端同时在访问
同时，这个 key 设置了较长的 TTL（如 24 小时）
然后这个 key 过期了

→ 大量客户端同时访问该 key → 触发大量惰性删除检查
→ 定期删除也在后台抽样
→ 瞬时 CPU 和 IO 压力增大
```

**缓解方案**：
- 避免大量 key 同时设置相同的过期时间
- 使用 TTL 加随机偏移（参见 [[Redis Value设计]] 缓存雪崩章节）
- 提高 `hz` 值加速定期删除

### 7.4 陷阱四：DEL 与过期时间

```bash
# DEL 无论 key 是否有过期时间，都立即删除
DEL mykey          # 立即删除，过期时间也一起清除

# 如果 key 有过期时间，DEL 之后该过期时间标记也消失
# （过期 key 本质上就是一个带时间戳的标记，DEL 直接移除）
```

### 7.5 陷阱五：复制延迟导致过期不一致

```
主从复制架构下：
  主库：key 已过期，但还没被删除（惰性/定期删除未触发）
  从库：主库的 DEL 命令还没同步过来

→ 从库上该 key 仍然"存在"并可被读取
→ 直到主库的 DEL 命令同步到从库
```

### 7.6 陷阱六：过期时间被覆盖

```bash
# 多次 EXPIRE 会覆盖之前的过期时间
SET mykey "value"
EXPIRE mykey 60        # 60 秒后过期
EXPIRE mykey 300       # 覆盖为 300 秒后过期
TTL mykey              # 返回约 300

# 用 SET EX 覆盖整个 key 时，原过期时间也消失
SET mykey "new_value" EX 120   # 新过期时间 120 秒
TTL mykey                      # 返回约 120
```

---

## 小结

- **过期不是立即删除**：`EXPIRE` 只是设置过期标记，实际删除靠惰性删除 + 定期删除协同完成
- **惰性删除**：key 被访问时检查，精确但被动，漏掉的 key 由定期删除兜底
- **定期删除**：后台定时随机抽样检查，主动但粗糙，两者互补确保所有过期 key 最终被清理
- **内存回收**：key 删除后内存归还到 jemalloc 池，不一定立即返还给 OS（RSS 不变是正常的）
- **常见陷阱**：过期时间不精确、批量过期抖动、主从复制延迟、多次 EXPIRE 覆盖
- **调优参数**：`hz`（扫描频率）、`active-expire-cycle-tries`（抽样次数）、`maxmemory-samples`（淘汰采样数）

<!-- KB:ANNOTATIONS -->
