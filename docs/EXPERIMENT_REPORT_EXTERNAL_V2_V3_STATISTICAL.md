# External Agent Statistical Evidence：external-openai-v2 vs external-openai-v3

阶段 7 在与阶段 6 相同的框架无关 Agent、模型、工具白名单、预算、Evaluator 和 Prompt Profile 下，将对照从 3 个 Case 扩展为 **8 Case × 3 Trial × 2 Version = 48 条真实 Trial**。每一条统计配对都是相同 `case_id` 与 `trial_index` 下的 v3 − v2；所有 Trial 均由平台独立执行测试、校验 Trace、采集 Git Diff。

## 可靠性与 Gate

| 指标 | v2 Baseline | v3 Candidate |
|---|---:|---:|
| Trial / valid pass | 24 / 24 | 24 / 24 |
| Evaluation pass rate | 100.0% | 100.0% |
| Pass@3 | 8 / 8 Case | 8 / 8 Case |
| Flaky Case Rate | 0.0% | 0.0% |
| Model / Trace / Infra failures | 0 / 0 / 0 | 0 / 0 / 0 |

既有 Gate 的 11 条规则全部通过，Decision 为 `promote`。这只表示 v3 没有触发正确性、可靠性或当前 10% 成本阈值的阻断规则；它不覆盖下面的统计诊断结论。

## 配对统计诊断

方法：以 Case 为聚类单位重采样（2,000 次、固定 seed `20260812`），每次保留该 Case 的全部有效配对 Trial，计算 Candidate − Baseline 平均差异的 95% percentile interval。它避免将同一个 Case 的三次重复误当成三个独立任务；不报告 p-value，也不以区间直接决定 Gate。

| 指标 | 点估计（平均 / 中位数 delta） | 95% 区间 | Case 胜负（v3 更低 / 更高 / 平） | 结论 |
|---|---:|---:|---:|---|
| Latency | -91 ms / -334 ms | -393 ms → +193 ms | 5 / 3 / 0 | **不确定**：区间跨 0，不能称整体延迟优化 |
| Model tokens | +212 / +136 | +73 → +433 | 2 / 6 / 0 | v3 Token 稳定增加 |
| Tool calls | +0.42 / 0 | +0.29 → +0.58 | 0 / 3 / 5 | v3 工具调用稳定增加 |

尾部指标也需要保留：v3 P50 latency 低 390 ms，但 P95 latency 高 1,682 ms；这与“平均略快”并不矛盾，说明 v3 的慢 Trial 风险更高。

## 结论

本轮可以做出的诚实结论是：**v3 与 v2 同样可靠、正确性不回归，符合当前 Gate 的可晋级条件；但没有足够证据证明它在 8 个 Case 上整体更快，并且它消耗更多 Token 和工具调用。**

因此不将 v3 包装为“性能优化版”。若继续演进，优先针对 `merge_settings_none`、`parse_port_blank_default` 等出现慢 Trial 的 Case 复盘 Trace，再提出一个新的、单变量可归因的 v4 假设。

## Artifact

- Experiment：`.runtime/external-openai-v2-v3-statistical/experiment.json`
- Gate：`.runtime/external-openai-v2-v3-statistical/gate-report.json`
- Evolution Catalog：`.runtime/evolution-catalog.json`
- 执行入口：`make external-statistical-evolution`

本报告不构成发布审计，也没有执行 GitHub 推送。
