# V3 → V3-negative 扩展 Benchmark 负向对照报告（V2）

## 结论

本实验验证 Gate 的拒绝能力。`external-openai-v3-negative` 保持 V3 的正常
修复循环与正确性，但在完成后额外执行两次不调用工具的模型完成。它在 11 个
Case 上仍全部通过测试，却使平均模型 Token 上升 **49.9%**；默认 Gate 因此
正确判定为 **`HOLD`**，独立审计通过。

这与同一 Benchmark 上 V3 → V4.1 的 `PROMOTE` 构成正反闭环：平台既能放行
可验证的优化，也能阻断“测试仍绿、成本却明显退化”的版本。

## 实验范围与可比性

- Artifact：`.runtime/external-openai-v3-negative-control-benchmark-v2/`
- Champion：`external-openai-v3` / `targeted-context-verify-v3`
- Negative：`external-openai-v3-negative` /
  `targeted-context-verify-v3-plus-two-redundant-completions`
- Benchmark：11 Case × 3 Trial × 2 Version，共 66 条选中 Trial
- Schedule seed：`20260821`
- Protocol fingerprint：`sha256:768f545f808b6d6a253f06b6e6b0401e57499a8dbc63089efce90e9dd8cc41a4`
- 运行时 Agent 源码 Hash：`sha256:967e4861b0c7daf84e6440275927dfa8761dd827920d9c812fc221c4f88662d7`
- Comparability：`strict`；允许差异仅为声明的终止后冗余完成行为

## 正确性与受控干预

| 指标 | V3 | V3-negative |
| --- | ---: | ---: |
| 选中 Trial 数 | 33 | 33 |
| 完成率 | 100% | 100% |
| 评测 / 测试通过率 | 100% | 100% |
| 模型失败率 | 0% | 0% |
| Trace 不完整率 | 0% | 0% |
| All-pass@3 | 11/11 Case | 11/11 Case |

审计确认每条负向 Trace 都有且仅有两条 `negative_control_redundant_call`，且其后
没有工具调用。因此成本退化可归因于预先声明的受控干预，而不是未冻结的 Agent
或平台变更。

## 成本退化与统计证据

| 指标 | V3 | V3-negative | Negative - V3 |
| --- | ---: | ---: | ---: |
| 平均工具调用 | 9.55 | 9.73 | +0.18 |
| 平均耗时 | 17.55s | 23.64s | +6.10s |
| 平均模型 Token | 16,115 | 24,153 | +8,038（+49.9%） |

按 Case 聚类的 95% Bootstrap 区间（2,000 次重采样）显示成本退化并非偶然：

- 耗时 Delta：`[+2.40s, +10.22s]`，9/11 Case 更高；
- Token Delta：`[+3,920, +13,123]`，9/11 Case 更高；
- 工具调用 Delta：`[-0.58, +0.91]`，结论不确定。

## Gate 与审计

- Gate：`HOLD`；
- 唯一硬阻断规则：`average_model_tokens_limit`（默认上限 +10%，实际 +49.9%）；
- 覆盖：11 个完整配对 Case、每臂 33 个选中 Trial；
- 审计：通过；每条负向 Trace 恰有两次冗余模型调用。

Gate 命令的非零退出码在这里代表预期的 `HOLD`，并非实验或审计失败。
