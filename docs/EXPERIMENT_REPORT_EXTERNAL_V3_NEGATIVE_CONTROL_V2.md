# V3 → V3-negative 正式负向对照报告（V2）

## 结论

本实验验证的是 Gate 的拒绝能力，而不是为负向版本争取发布资格。`external-openai-v3-negative` 保持 V3 的 Prompt 和正常修复循环，并在终止后额外执行两次不调用工具的模型完成。它保持了修复正确性与 Trace 完整性，但平均模型 Token 增加 **18.7%**，超过默认 Gate 的 10% 成本上限，因此 Gate 正确判定为 **`HOLD`**。

这说明平台不会仅因所有测试通过就放行成本退化的 Agent 版本。

## 实验范围与证据身份

- Artifact：`.runtime/external-openai-v3-negative-control-v2/`
- Baseline：`external-openai-v3` / `targeted-context-verify-v3`
- Negative：`external-openai-v3-negative` / `targeted-context-verify-v3-plus-two-redundant-completions`
- Benchmark：8 Case × 3 Trial × 2 Version，共 48 个已选中 Trial
- Schedule seed：`20260818`
- Protocol fingerprint：`sha256:8034856c362da5f06588392035f7735afdd04002e05db4bb3c30cc1d70810b2c`
- 运行时 Agent 源码 Hash：`sha256:967e4861b0c7daf84e6440275927dfa8761dd827920d9c812fc221c4f88662d7`
- Comparability：`strict`；48/48 个已选中 Trial 的运行时 Hash 与冻结协议一致

该目录是加入运行时源码身份校验后的全新实验。早期负向实验只保留为历史诊断，不再作为同等严格的正式证据。

## 正确性与可靠性

| 指标 | V3 | V3-negative |
| --- | ---: | ---: |
| Trial 数 | 24 | 24 |
| 完成率 | 100% | 100% |
| 评测 / 测试通过率 | 100% | 100% |
| 模型失败率 | 0% | 0% |
| Trace 不完整率 | 0% | 0% |
| All-pass@3 | 8/8 Case | 8/8 Case |

一个 `dependency_cycle_detection` Trial 曾保留 provider timeout 与缺失 `.env` 的配置失败 Attempt；后续 `attempt_003` 在同一冻结协议和同一源码 Hash 下通过。报告只读取显式选中的终态 Attempt，但不删除此前失败证据。

## 受控干预与效率

审计确认每条负向 Trace 都有且仅有两条 `negative_control_redundant_call`，且其后没有工具调用。

| 指标 | V3 | V3-negative | Delta |
| --- | ---: | ---: | ---: |
| 平均工具调用 | 8.79 | 8.08 | -0.71 |
| 平均耗时 | 12.91s | 14.94s | +2.04s |
| 平均模型 Token | 12,960 | 15,383 | +2,423（+18.7%） |

Case 聚类 Bootstrap 的 Token、耗时和工具调用区间均跨过 0，因此统计结论为 `inconclusive`。这不削弱 Gate 的成本上限结论：Gate 以完整配对 Trial 的观察到的平均 Token 相对增幅执行硬阈值判定，并明确输出 `HOLD`。

## Gate 与审计

- Gate：`HOLD`
- 唯一硬阻断规则：`average_model_tokens_limit`
- 审计：通过（24 条 Champion、24 条 Negative、每条 Negative 两次冗余调用）

该结论与正向 V3 → V4.1 的 `PROMOTE` 互补：前者证明平台能接受经验证的效率改进，后者证明平台能拒绝保持正确性但突破预算的版本。
