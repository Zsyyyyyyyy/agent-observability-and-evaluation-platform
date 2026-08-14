# V3 → V4.1 完整 Benchmark 报告

## 结论

V4.1 在本轮 8 Case × 3 Trial × 2 Version 的 48 条真实 Trial 中通过 Gate，且没有牺牲修复正确性或 Trace 完整性。统计证据支持本轮覆盖范围内的效率改善；不将其扩展为未覆盖场景的普遍保证。

## 实验信息

- Artifact：`.runtime/external-openai-v3-v4-1-benchmark/`
- Baseline：`external-openai-v3` / `targeted-context-verify-v3`
- Candidate：`external-openai-v4.1` / `bounded-success-stop-verify-v4-1`
- Model：`deepseek-v4-flash`
- Sampling：`temperature=0.0`、`top_p=1.0`、`seed=not_configured`
- Schedule seed：`20260814`
- Protocol fingerprint：`sha256:e1ce398d6e01044f8b1e118dd0902dedaf4a3297b7e2154ab025194ac16ebf7e`
- Comparability：`strict`

## 质量与可靠性

| 指标 | V3 | V4.1 |
| --- | ---: | ---: |
| Trial 数 | 24 | 24 |
| 完成率 | 100% | 100% |
| 评测通过率 | 100% | 100% |
| Trace 不完整率 | 0% | 0% |
| 模型失败率 | 0% | 0% |
| All-pass@3 | 8/8 Case | 8/8 Case |

V4.1 的 24 条 Candidate Trace 均记录 `agent.stop: verification_passed_policy`；该事件之后的 `model.call` / `tool.call` 数量为 **0**。

## 效率与行为

| 指标 | V3 | V4.1 | Delta |
| --- | ---: | ---: | ---: |
| 平均工具调用 | 8.50 | 6.83 | -1.67 |
| 平均耗时 | 15.12s | 7.35s | -7.77s |
| 平均模型 Token | 12,691 | 5,719 | -6,972 |
| 重复工具调用率 | 28.15% | 15.77% | -12.38pp |
| 被拒工具尝试 | 11 | 0 | -11 |
| 重复读取率 | 6.46% | 0% | -6.46pp |

按 Case 聚类 Bootstrap（95% CI，24 组配对 Trial）：

- 耗时平均 Delta：`-7,771.8ms`，CI `[-11,357.3ms, -4,449.8ms]`；
- 模型 Token 平均 Delta：`-6,971.7`，CI `[-11,021.0, -3,167.6]`；
- 工具调用平均 Delta：`-1.67`，CI `[-2.67, -0.67]`。

三项区间均低于 0，且 Case 方向没有出现 Candidate 变差的 Case；工具调用有 3 个 Case 持平、5 个 Case 减少。

## Gate

Gate：`PROMOTE`。所有硬规则通过，Protocol 严格可比，平均 Token 和工具调用均未超过成本阈值。

## 边界

这是一轮完整的 8 Case Benchmark，但 Case 仍是项目内构造的修复任务，不等价于所有真实生产 Agent。后续若要发布 V4.1，应保留本报告中的 Trace、Protocol Hash、Case 覆盖和统计区间，不只展示均值或 Gate 状态。
