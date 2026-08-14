# Benchmark v2 扩展 Case 实验审计

## 审计范围

- Artifact：`.runtime/external-openai-v2-v3-benchmark-v2-rerun/`
- Baseline：`external-openai-v2`
- Candidate：`external-openai-v3`
- Case：`batch_partial_failure_isolation`、`dependency_cycle_detection`、`profile_v1_migration`
- 样本：3 Case × 3 Trial × 2 Version，共 18 次真实模型执行
- 模型：`deepseek-v4-flash`
- 调度：固定 `schedule_seed=20260813` 的成对交错计划

## 完整性与可比性

18 次计划执行均有完成结果，Baseline 与 Candidate 各 9 次；没有 Model、Infrastructure、Trace、Path Policy 或 Diff Policy 故障。Experiment 中的 Protocol Comparability 为 `strict`，Gate 的 `protocol_strict_comparability` 规则通过。

本轮旧版 Protocol 仍有两个已知边界：采样参数记录为 `null`，两个版本的 Prompt Profile 仅记录了相同 Agent 源文件 Hash。它们不影响对既有 Artifact 的读取，但下一轮不得继续沿用该冻结方式。Protocol schema v2 已改为记录明确采样值和最终渲染 Prompt 集合 Hash；不回写本轮 Protocol，避免伪造历史证据。

## Gate 结论

当前默认 Gate 的 12 条规则全部通过，Decision 为 `promote`。这个 Decision 的准确含义是：在该 3-Case Benchmark Slice 中，Candidate 没有违反当前正确性、可靠性、成本和协议可比性阈值。

它不等于“已统计证明 Candidate 全面更快、更省”。Gate 是发布政策判断，Bootstrap 是统计证据判断，两者必须分开解释。

| 指标 | Baseline | Candidate | 观察值 |
|---|---:|---:|---:|
| Evaluation pass rate | 88.9% | 100% | +11.1pp |
| all-pass@3 | 66.7% | 100% | +33.3pp |
| Flaky Case rate | 33.3% | 0% | -33.3pp |
| 平均耗时 | 25.36s | 19.06s | -6.29s |
| 平均 Model Tokens | 22,032 | 18,232 | -3,801 |
| 平均 Tool Calls | 10.78 | 10.00 | -0.78 |

## 关键失败定位

唯一无效通过是 Baseline 的 `profile_v1_migration_trial_002`：

- 测试、Trace、路径策略和 Diff 策略全部通过；
- 实际产生 19 次 Tool Call，超过预算上限 18；
- Agent Exit Reason 为 `max_tool_calls`；
- 失败归因为 `agent_budget_exceeded`；
- Trace 显示修复和验证完成后仍继续探索，出现重复 `glob`、额外 `bash`、被拒绝的 `write_file` 和一次失败 `read_file`。

对应 Candidate Trial 使用 10 次 Tool Call，在预算内完成。这是当前最适合用于 `Gate → Case → Trial → Trace` 演示的行为差异。

## 统计边界

Clustered Case Bootstrap 只包含 3 个 eligible Cases，报告正确标记为 `limited_coverage`；项目设定至少 8 个 Case 才能支持宽泛性能主张。

排除一个失败配对后，8 个有效配对的结果为：

| 指标 | 配对均值 Delta | 95% CI | 结论 |
|---|---:|---:|---|
| Duration | -2.15s | [-4.46s, +1.31s] | inconclusive |
| Model Tokens | +441 | [-3,240, +3,736] | inconclusive |
| Tool Calls | +0.25 | [-1.00, +1.33] | inconclusive |

三个置信区间都跨过 0。因此可对外表述为：

> v3 在这三个扩展 Case 上把 all-pass@3 从 2/3 提升到 3/3，并消除了一个预算型 Flaky Case；效率方向仍因覆盖不足而不确定。

不能表述为：

> v3 已被证明整体更快、更省 Token、更少调用工具。

## 下一步

1. 前端把本轮结果呈现为 `Gate promote + statistical evidence inconclusive`，不得只显示绿色 PASS。
2. 将 `profile_v1_migration_trial_002` 作为首个回归贡献者，串联成对 Trial 与 Trace 行为差异。
3. 下一轮实验使用 Protocol schema v2，确认 Prompt Hash 与请求采样参数均已冻结。
4. 从扩展 Benchmark 中选择至少 8 个有区分度的 Case，再执行统计实验；在此之前不重复运行本轮 18 次模型调用。

## Artifact

- Experiment：`.runtime/external-openai-v2-v3-benchmark-v2-rerun/experiment.json`
- Frozen Protocol：`.runtime/external-openai-v2-v3-benchmark-v2-rerun/protocol.json`
- Execution Plan：`.runtime/external-openai-v2-v3-benchmark-v2-rerun/execution-plan.json`
- Gate：`.runtime/external-openai-v2-v3-benchmark-v2-rerun/gate-report.json`
