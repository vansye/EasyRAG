---
type: knowledge
title: Redis 内存淘汰
created: 2026-08-14
updated: 2026-08-14
tags:
  - Redis
  - 内存管理
  - 淘汰策略
  - LRU
  - LFU
source: 无
conclusion: Redis 通过配置 maxmemory 和淘汰策略（如 LRU、LFU、TTL 等）来管理内存上限，在内存不足时按规则自动移除部分键，确保服务持续可用。
---

## 详细

### 概念
内存淘汰（Eviction）是指当 Redis 使用的内存达到配置的 `maxmemory` 上限时，根据预设的淘汰策略，自动删除部分键以释放内存空间。这是 Redis 作为内存数据库的关键自我保护机制。

### 重点

#### 一、八种淘汰策略

| 策略名 | 行为 | 适用场景 |
| :--- | :--- | :--- |
| **noeviction** | 不淘汰，写操作返回错误 | 不允许数据丢失的严格场景 |
| **allkeys-lru** | 所有键中淘汰最近最少使用（LRU）的键 | 热点数据缓存，通用推荐 |
| **volatile-lru** | 仅设过期时间的键中淘汰 LRU | 需区分永久与临时数据 |
| **allkeys-lfu**（4.0+）| 所有键中淘汰最不经常使用（LFU）的键 | 访问频率差异明显的场景 |
| **volatile-lfu**（4.0+）| 仅设过期时间的键中淘汰 LFU | 兼顾冷热数据与过期策略 |
| **allkeys-random** | 所有键中随机淘汰 | 数据访问无热点且需简单策略 |
| **volatile-random** | 仅设过期时间的键中随机淘汰 | 有到期数据且分布均匀 |
| **volatile-ttl** | 优先淘汰剩余生存时间（TTL）最短的键 | 需优先清理即将过期的数据 |

#### 二、LRU vs LFU
- **LRU（Least Recently Used）**：淘汰最久未被访问的键，关注时间维度。Redis 采用**近似 LRU**（采样淘汰），在性能与准确性间取得平衡，通过 `maxmemory-samples` 控制采样数量（默认 5）。
- **LFU（Least Frequently Used）**：淘汰访问频率最低的键，关注次数维度。Redis 4.0 引入，通过计数器和衰减算法识别长期冷数据，需在配置中启用。

#### 三、配置示例
```conf
maxmemory 4gb
maxmemory-policy allkeys-lru
maxmemory-samples 5        # 采样大小，越大越精确但消耗 CPU
lfu-decay-time 1           # LFU 衰减因子
lfu-log-factor 10          # LFU 计数增长因子
```
#### 四、适用场景速查

-  **通用缓存**：`allkeys-lru`
- **访问频率差异大**：`allkeys-lfu`
- **临时数据为主（如 Session）**：`volatile-ttl` 或 `volatile-lru`
- **数据均无过期时间**：只能选 `allkeys-*` 或 `noeviction`
- **不允许丢失任何数据**：`noeviction` + 监控扩容