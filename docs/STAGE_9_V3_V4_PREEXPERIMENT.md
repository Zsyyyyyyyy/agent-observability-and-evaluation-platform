# 阶段 9：V3 → V4 预实验执行清单

## 目的与边界

验证 `success-stop-verify-v4` 是否能在不降低修复质量的条件下，降低 V3 的成功后冗余探索、被拒工具调用和预算超限风险。

这是一次**预实验**，不是对 V4 的广泛性能宣称：3 个 Case 只能验证风险方向和可行性，不能替代至少 8 Case 的完整统计验证。

## 已冻结的比较设计

| 项目 | 固定值 |
|---|---|
| Baseline | `external-openai-v3` / `targeted-context-verify-v3` |
| Candidate | `external-openai-v4` / `success-stop-verify-v4` |
| 唯一计划变量 | 渲染后的 Prompt Profile |
| Case | `dependency_cycle_detection`、`batch_partial_failure_isolation`、`profile_v1_migration` |
| 重复 | 每个版本、每个 Case 3 次 |
| 总量 | 18 条真实 Trial |
| 调度 | 成对交错，`schedule_seed=20260813` |
| 工具 | `read_file`、`write_file`、`edit_file`、`glob`、`bash` |
| 路径策略 | 仅允许修改 `src/**`；禁止修改 `tests/**` |
| 预算 | 每个 Case：18 Tool Calls、18,000 Model Tokens、180 秒 |
| 输出目录 | `.runtime/external-openai-v3-v4-preexperiment/`（新目录，禁止复用旧 Benchmark） |

正式执行前，运行器会用 Protocol schema v2 冻结：模型标识、温度、`top_p`、seed、Case/fixture/test/policy Hash、Agent 源码 Hash，以及 V3/V4 各自的**渲染 Prompt 集合 Hash**。密钥不会写入 Artifact。

## 运行命令

先在项目根目录载入既有模型环境变量，再执行：

```bash
set -a; source .env; set +a
make external-v4-preexperiment
```

该命令会调用真实模型，预计执行 18 次 Agent Trial。它不带 `--resume`，避免把新 Prompt 或新采样协议混入旧 Artifact；如中断，先审计 `protocol.json` 与失败 Trial，再决定是否针对该新目录安全续跑。

## 执行后验收顺序

1. **协议完整性**
   - `protocol.json` 为 schema v2；
   - V3/V4 的 `rendered_prompt_set_hash` 不相同；
   - `temperature`、`top_p` 为明确数值；seed 为明确值或 `not_configured`；
   - `experiment.json` 的 comparability 为 `strict`。
2. **数据质量**
   - 执行计划 18 项均有选中 Attempt；
   - 区分 `model_failed` / `infra_failed` 与 Agent 失败；前两类存在时先输出 `INCONCLUSIVE`，不归因给 V4；
   - Trace、路径策略和 Diff 证据完整。
3. **V4 假设验证**
   - V4 平台有效通过率不得低于 V3；
   - V4 不得新增 Agent、Trace、路径策略或预算失败；
   - 重点比较尾部 Tool Calls、`denied_tool_attempts`、重复工具调用率、重复读取率；
   - `profile_v1_migration` 是重点 Drill-down Case，但不是唯一判据。
4. **决策**
   - 质量回归：停止，记录 `reject` 或 `inconclusive`；
   - 质量不回归且行为风险改善：再进入 ≥8 Case 的完整统计实验；
   - 不以本轮 3 Case 的均值宣称 V4 已整体更快或更省。

## 与当前前端诊断链路的关系

当前首页展示的是已冻结的 `v2 → v3` 诊断证据：`Gate: PROMOTE`、`Statistical evidence: INCONCLUSIVE`、`Coverage: 3/8 Cases`。V3/V4 预实验会写入独立 Artifact，完成质量验收前不替换该展示结论。
