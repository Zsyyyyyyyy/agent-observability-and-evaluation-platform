# V3 → V4.1 扩展 Benchmark 正式报告（V2）

## 结论

在冻结后的 11-Case Benchmark 上，`external-openai-v4.1` 相对
`external-openai-v3` 保持 100% 完成与评测通过率，同时显著降低耗时、模型
Token 与工具调用。默认 Gate 判定为 **`PROMOTE`**，独立审计通过。

这是替代先前 8-Case 正向证据的扩大范围结果；两份证据各自只适用于其冻结的
Case 集，不能拼接计算平均值或置信区间。

## 实验范围与可比性

- Artifact：`.runtime/external-openai-v3-v4-1-benchmark-v2/`
- Baseline：`external-openai-v3` / `targeted-context-verify-v3`
- Candidate：`external-openai-v4.1` / `bounded-success-stop-verify-v4-1`
- Benchmark：11 Case × 3 Trial × 2 Version，共 66 条选中 Trial
- Schedule seed：`20260820`
- Protocol fingerprint：`sha256:9ff49800a38fd197d457ec89873dd334870d80abe10df14f4f370094ba3ccdba`
- 运行时 Agent 源码 Hash：`sha256:967e4861b0c7daf84e6440275927dfa8761dd827920d9c812fc221c4f88662d7`
- Comparability：`strict`；所有选中 Trial 的运行时 Hash 与冻结协议一致

新增 Case 覆盖批处理原子性、分页游标版本边界和 Webhook 签名规范化，避免结论
只依赖原先的 8 个修复任务。

## 正确性与可靠性

| 指标 | V3 | V4.1 |
| --- | ---: | ---: |
| 选中 Trial 数 | 33 | 33 |
| 完成率 | 100% | 100% |
| 评测 / 测试通过率 | 100% | 100% |
| 模型失败率 | 0% | 0% |
| Trace 不完整率 | 0% | 0% |
| All-pass@3 | 11/11 Case | 11/11 Case |

## 效率与统计证据

| 指标 | V3 | V4.1 | Candidate - Baseline |
| --- | ---: | ---: | ---: |
| 平均工具调用 | 9.61 | 6.79 | -2.82 |
| 平均耗时 | 18.75s | 7.54s | -11.21s |
| 平均模型 Token | 17,589 | 5,929 | -11,660（-66.3%） |

按 Case 聚类的 95% Bootstrap 区间（2,000 次重采样）均未跨越 0：

- 耗时 Delta：`[-16.58s, -6.44s]`，11/11 Case 更低；
- Token Delta：`[-16,721, -7,002]`，11/11 Case 更低；
- 工具调用 Delta：`[-4.03, -1.76]`，8/11 Case 更低、3 个持平。

## Gate 与审计

- Gate：`PROMOTE`，无硬阻断规则；
- 覆盖：11 个完整配对 Case、每臂 33 个选中 Trial；
- 审计：通过；33 条 V4.1 Trace 都在 `verification_passed_policy` 后停止，
  没有后续模型或工具 Span。

该结果证明 V4.1 的成功终止策略不是以牺牲修复正确性换取成本下降。
