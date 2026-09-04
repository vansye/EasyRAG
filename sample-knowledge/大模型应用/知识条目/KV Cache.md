---
type: knowledge
title: KV Cache
created: 2026-07-24T09:00:00
updated:
tags: []
source:
conclusion:
---

## 详细

# KV Cache（键值缓存）深度知识概览

## 一、 核心定义：什么是 KV Cache？
KV Cache 是在**自回归语言模型**（如 GPT、LLaMA）推理过程中，将前面已生成 Token 的 **Key（键）** 和 **Value（值）** 矩阵保存下来，供后续 Token 生成时重复使用的一种缓存机制。

**通俗理解**：写文章时，你不需要每次写下一个字时都把前面写过的所有字重新读一遍并记住，你只需要“瞟一眼”上一段写到了哪里。KV Cache 就是大模型的那双“瞟一眼”的眼睛。

## 二、 为什么需要 KV Cache？（算力与时间的博弈）

在没有 KV Cache 的情况下，生成第 N 个 Token 时，模型需要把前 N-1 个 Token 的完整序列重新输入计算一遍。这会导致：

- **计算复杂度 O(N²)**：序列越长，重复计算量呈平方级增长。
- **极度浪费算力**：前面的 Token 在每生成一个新字时都会被重复计算，且计算结果（K、V）实际上没有变化。

**引入 KV Cache 后**：每个 Token 的 K、V 只在**第一次出现时计算一次**，后续生成新 Token 时直接复用缓存中的值，复杂度降为 **O(N)**，生成速度提升数十倍甚至上百倍。

## 三、 工作机制（底层原理解析）

以标准 Decoder-Only 架构（如 GPT）的自回归生成为例：

### 1. 推理阶段划分
- **Prefill（预填充阶段）**：用户输入 Prompt 时，模型并行计算所有输入 Token 的 K、V，并生成第一个输出 Token（此时会填充初始 KV Cache）。
- **Decode（解码阶段）**：逐个生成后续 Token。**每步只计算当前最新 Token 的 Q、K、V**，将当前 Token 的 K、V 追加（Append）到缓存中，而 Q 用于与缓存中所有 K 做注意力计算。

### 2. 注意力计算的变化
传统 Attention：`Softmax(Q_all · K_all^T) · V_all`（所有 Token 参与）。
**带 KV Cache**：`Softmax(Q_new · K_cached^T) · V_cached`（新 Query 只与历史缓存的 KV 交互）。

## 四、 内存开销：巨大的“吞金兽”（显存瓶颈）

KV Cache 是典型的**以显存换时间**的策略，其内存占用非常惊人，是推理部署的最大瓶颈。

**计算公式**：
`总显存占用 = 2（K和V） × batch_size × seq_len × num_layers × hidden_size × precision_bytes`

**例子**：LLaMA-7B（32层，hidden_size=4096），运行 batch_size=32，seq_len=2048，FP16（2字节）下：
`2 × 32 × 2048 × 32 × 4096 × 2 ≈ 34 GB`
> 这**远超**模型权重本身（约 14GB）的内存占用！

## 五、 针对 KV Cache 的顶尖优化策略

既然 KV Cache 如此占显存，业界有以下几大杀手锏技术：

### 1. 多查询注意力（MQA）与分组查询注意力（GQA）
- **MHA（传统多头）**：每个注意力头都有独立的 K、V，Cache 极大。
- **MQA（多查询注意力）**：所有头**共享**同一组 K、V。显存暴降，但精度略有损失（Google PaLM 采用）。
- **GQA（分组查询注意力）**：将 Head 分组，每组共享 K、V，是 MHA 和 MQA 的折中方案（**LLaMA-2/3 主力采用**）。

### 2. PagedAttention（分页注意力）—— vLLM 框架的核心
- **痛点**：传统 KV Cache 需要预先分配连续显存，导致内存碎片化和闲置浪费。
- **解法**：仿照操作系统虚拟内存，将 KV Cache 切分成固定大小的“页”（Pages），存储在非连续的物理显存中，通过逻辑块映射查找。**显存利用率提升至接近 100%**。

### 3. 连续批处理（Continuous Batching）
- 传统推理需等整个序列生成完毕才能处理下一批请求。
- 利用 KV Cache 的独立性，在 Token 粒度动态调度请求（生成完毕的立刻退出，新的请求立即插入空位），极大提高 GPU 利用率。

### 4. FlashAttention / FlashDecoding
- 通过精细的 GPU 内存层级（HBM -> SRAM）分块计算，避免频繁读写 KV Cache 带来的带宽瓶颈，加速 Attention 计算过程。

## 六、 RAG 与 KV Cache 的密切关系

当你搭建 RAG 系统时，KV Cache 的管理变得尤为关键：

- **长上下文挑战**：RAG 通常会塞入 Top-5 或 Top-10 的文本块，Prompt 动辄数千甚至上万 Token。**过长的 Prompt 会导致 Prefill 阶段极慢，且 KV Cache 瞬间撑爆显存。**
- **应对策略**：
  1. **上下文压缩**（减少检索块的长度）。
  2. **重排序（Re-rank）后只取精华**（减少 Top-K 数量）。
  3. 部署时务必开启 **GQA** 架构的模型（如 LLaMA-3）并配合 **vLLM** 框架，否则 RAG 的长上下文吞吐量会惨不忍睹。

## 七、 总结：关键时间节点速记
| 维度            | 要点                                            |
| :------------ | :-------------------------------------------- |
| **本质**        | 空间（显存）换时间（算力）的经典工程实践。                         |
| **最怕什么**      | 超长上下文 + 超大并发（显存爆炸）。                           |
| **目前最优解**     | GQA 架构 + vLLM（PagedAttention）+ FP8/INT4 量化缓存。 |

> 个人实践见 PromptOptimizer（popt HTTP 代理，自动优化 KV Cache 命中）。
| **与 RAG 的关系** | RAG 的长 Prompt 极度依赖高效的 KV Cache，否则无法落地。        |
