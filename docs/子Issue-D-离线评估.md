# 子 Issue D：离线评估入口适配与回归

Issue：[#37](https://github.com/vansye/EasyRAG/issues/37)，父 Issue：#1。当前设计：[FastAPI 模块迁移](fastapi-modules.md)。本次只适配既有离线工具，不增加评估数据库或网页功能。

## 功能与数据原型

用户运行现有黄金集，获得可复现的召回指标和报告。D 独占样例选择、题集解析、指标口径及报告生成；不参与线上索引或问答许可。

```python
Question = {id, category, question, expected_sources}
EvaluationInput = {corpus_path, questions_path, model, chunk_parameters}
EvaluationResult = {rankings, hit_at_k, metrics_by_category, timings}
evaluate(input: EvaluationInput) -> EvaluationResult
render_report(result: EvaluationResult) -> str
```

## 边界

- 复用切片时只导入 B 的公开入口；不访问 B 的 private 实现或应用全局对象。
- 继续使用独立样例语料、测试索引/向量和结果路径，不能修改业务 MySQL、Chroma 或模型配置。
- 本次保持现有运行参数、召回和计分逻辑；不把接口迁移当作检索质量提升。
- 招新文档中的改写、多路检索、评估界面属于后续能力，不在此次改架构时顺带实现。

## PR 与验收

- [x] 更新公开切片入口调用，CLI 帮助和原有离线测试通过（PR #47）。
- [x] 相同 fixture、参数和向量输入得到同一切片/排名/计分输出（PR #47、#48）。
- [x] 报告记录环境、模型和实际测量，不虚构实时性能或检索提升。
- [x] 无业务目录、密钥文件或个人知识库写入。

实现与验证见 [PR #47](https://github.com/vansye/EasyRAG/pull/47)。本次迁移未重新测量召回质量，不把架构调整描述为 hit@k 提升；历史基线报告保持原记录。

## 在线问答验收（2026-09-21，M4 优先级 4/5 的度量衡）

离线召回脚本不调用模型，所以它测不出三件事：库外题是否真的被拒答、答案引用的是不是用户资料里的那一篇、句子是否越出了片段。判断力记录早已定下"拒答正确率只能由 LLM 判"，此前靠在线手工跑记表，不可复跑。`scripts/eval_answers.py` 把它脚本化：

```python
EvaluationInput = {corpus_path, golden_sets[], embedding, top_k, faithfulness}
QuestionResult = {decision, status | failure, candidates[rank, source, score], expected_rank,
                  cited_ranks, cited_sources, wrong_source, first_text_ms, total_ms, stage_ms,
                  model_calls, generate_prompt_chars, supported, unsupported, uncited_sentences}
evaluate(input) -> {results[], summary{categories, sources, faithfulness, latency_ms, calls}}
```

- 样例语料在纯 ASCII 临时目录建隔离索引（B 的真实切片与 embedding），经 C 的真实判定/生成得到结果；只 import 各模块 `public`，不读业务 MySQL，不开业务 Chroma，不写历史。
- 指标：各类判定符合率（N 类 = 拒答正确率，Q/R 不符合 = 误拒，P 类 SUFFICIENT = 冒充完整）；**错源率** = 已作答且有引用的 Q/R 中引用片段全部不属于标注出处的比例——判定器与引用形态校验都抓不住它，只有检索层能防；`--faithfulness` 用同一模型逐句核对"是否完全由所引片段支持"得到**引用支持率**，无引用句只计数；首段非空文本与完整耗时的中位数/最大值作为"端到端延迟"的唯一口径；模型调用次数与生成提示字符数逐题记录。
- 报告与同名 JSON（含候选与完整答案）写入 `docs/eval/`，命令行只记录相对路径。

| # | 裁决 | 理由 |
|---|---|---|
| D-1 | 拒答正确率、错源率、引用支持率只在线测，不在离线召回脚本里冒充 | 三者都依赖模型判断或生成结果；离线脚本"只展示召回不进分母"的口径保持不变 |
| D-2 | 错源率作为独立指标，且区分"标注出处未进候选"与"进了候选但没被引用" | Q21 型失败的答案引用合法、内容也未必编造，现有防线全部放行；两种成因对应检索与生成两个不同的改进点 |
| D-3 | 支持率核对用同一回答模型、逐句、二值输出；以冒号结尾的引导句和纯加粗标题不算句子，无引用句不核对 | 换更强的判官模型会把"判官能力"混进指标；逐句比整段更能定位越界；首跑发现"无引用句"里全是"特点如下："和加粗小标题，它们不是事实陈述 |
| D-4 | 报告记录本次样本数字，不把它当概率保证 | 模型输出每次运行可能不同；前后对照必须同一题集、同一策略、同一模型 |
| D-5 | 评估脚本自己节流：相邻模型调用停顿、上游不可用时冷却后整题重跑并记录重试次数；产品代码的"不自动重试"不变 | 首跑 42 题里 35 题在判定阶段 80 毫秒内被第三方接口拒绝——密集的逐句核对触发了限流，而 F 把一切失败都包成 `ModelUnavailable`；重跑只保留最后一次尝试的耗时，契约失败（如 INVALID_CITATIONS）不重试 |

不在本节内：评估数据库、网页看板、自动调参。

### 首跑基线解读（2026-09-21，纯稠密检索，报告 `eval/answers-baseline-2026-09-21.md`）

- 判定：Q 22/22、R 8/8 未误拒（其中 6 题判 PARTIAL，偏保守但仍作答）；N 类拒答 10/10——v2 的三道"主题在库、内容缺失"陷阱题（N8–N10）全部拒答；P 类 1/2，P2（PagedAttention 四行概要）被判 SUFFICIENT 冒充完整。
- 错源：Q21 标注出处未进候选，判定器看着 Redis 持久化片段判 SUFFICIENT，答案引用 [1][2][3] 全是 Redis——引用合法、判定合法、内容未编造，但答错了篇。它是 M4 混合检索的"前"数字（错源 1/30，标注出处进候选 29/30）。
- 引用支持率 86.9%（192/221，19 题各有 1–3 句）。对不支持最多的 5 题抽查（本机记录，不入库）后，不支持句分三类：把缩写展开成片段里没有的全称（Q12 的 LRU/LFU）、在片段基础上做推理性补充（R7 的"不一定能在到期瞬间全部清理掉"）、接近改写的复述（Q3）；没有发现编造事实。同一题两次运行的句子级结果差异明显（R2 一次 1/3、一次 7/0），支持率只能看总量趋势，不能逐题比较。
- 延迟：首段文本中位 2.6 s（最大 12.6 s），完整中位 6.6 s（最大 23.8 s），拒答 1.7 s；分段中位 embedding 0.94 s、判定 0.76 s、生成 5.8 s；生成提示中位 1846 字符。生成占八成以上，与 9-15 的测量一致。
- 判断：幻觉的主要入口在检索层（错源）而不是生成层（越界轻微且噪声大），优先级维持"混合检索 → 判定器标出相关片段/证据压缩"，后者以支持率总量不降、错源不升、生成耗时下降为门槛。
- 运行纪律：第一次全量因逐句核对密集调用触发第三方限流，35/42 题在判定阶段被拒（D-5 的由来）；带节流重跑 300 次调用、3 题各重试一次即成功。
