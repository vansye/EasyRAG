# 子 Issue D：离线评估入口适配与回归

父 Issue：#1。当前设计：[FastAPI 模块迁移](fastapi-modules.md)。本次只适配既有离线工具，不增加评估数据库或网页功能。

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

- [ ] 更新公开切片入口调用，CLI 帮助和原有离线测试通过。
- [ ] 相同 fixture、参数和向量输入得到同一切片/排名/计分输出。
- [ ] 报告记录环境、模型和实际测量，不虚构实时性能或检索提升。
- [ ] 无业务目录、密钥文件或个人知识库写入。
