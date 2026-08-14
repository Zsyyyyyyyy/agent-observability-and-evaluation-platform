# External Agent Evolution：external-openai-v2 vs external-openai-v3

阶段 6 使用同一个框架无关的 `external_openai_agent.py`，在相同模型、工具白名单、预算、3 个 Case 和重复次数下比较两个 Prompt Profile。平台生成 18 条真实 Trial（3 Case × 3 Trial × 2 Version），随后使用既有 `default-gate` 评估，没有重写 Gate；最终选中的 18 条 Trial 均为平台证据完整的 `completed`。

## 结果摘要

| 指标 | v2 Baseline | v3 Candidate | Delta（v3 - v2） |
|---|---:|---:|---:|
| Evaluation pass rate | 100.0% | 100.0% | 0.0pp |
| Model failure rate | 0.0% | 0.0% | 0.0pp |
| 平均耗时 | 10,839.9 ms | 9,982.1 ms | **-857.8 ms** |
| P50 耗时 | 10,816.2 ms | 10,035.6 ms | **-780.6 ms** |
| P95 耗时 | 12,111.5 ms | 10,479.9 ms | **-1,631.6 ms** |
| 平均 Model Token | 5,113.2 | 5,137.2 | +24.0 |
| 平均工具调用 | 5.56 | 5.67 | +0.11 |
| Pass@3 | 100.0% | 100.0% | 0.0pp |
| Flaky Case Rate | 0.0% | 0.0% | 0.0pp |

## Gate 结论

`gate-report.json` 的 11 条规则全部通过，Decision 为 `promote`。这表示 v3 满足当前发布政策的正确性与可靠性要求；它不是严格意义上的成本优化，因为 Token 和工具调用略有增加。平均耗时和 P95 属于诊断指标，均有改善。

## 演进记忆

Evolution Catalog 已保存：

- `external-openai-v1 → external-openai-v2`：首次记录；
- `external-openai-v2 → external-openai-v3`：`strictly comparable`，因为 Case、fixture/test、tool policy、evaluator 和重复次数指纹一致；
- `external-openai-v3` 的 `parent_version_id` 指向 v2；
- Gate Decision 与 v2/v3 Experiment 通过 `experiment_id` 关联，旧实验和旧决策未被覆盖。

## Artifact

- Experiment：`.runtime/external-openai-v2-v3/experiment.json`
- Gate：`.runtime/external-openai-v2-v3/gate-report.json`
- Catalog：`.runtime/evolution-catalog.json`
- 执行入口：`make external-evolution`
