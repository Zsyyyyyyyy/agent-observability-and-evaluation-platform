# Stage 12：扩展 Benchmark 正式实验协议

## 目标

将先前 8 Case 的正、负 Gate 验证扩展为 11 Case。新增的任务覆盖事务性状态更新、版本化分页边界和安全签名规范化；旧实验保留为其原始 8 Case 范围内的证据，不能与新 Case 直接拼接。

## 两个独立实验

| 实验 | 对照 | 真实 Trial | 预期 Gate |
| --- | --- | ---: | --- |
| 正向确认 | V3 vs V4.1 | 11 × 3 × 2 = 66 | `PROMOTE`（已验证） |
| 负向挑战 | V3 vs V3-negative | 11 × 3 × 2 = 66 | `HOLD`（已验证） |

两个实验不共享 Artifact 目录或执行计划。它们共享 Benchmark 定义、模型环境、工具策略、默认 Gate 和运行时源码身份校验，但使用不同的随机调度种子。每个真实 Trial 仍在隔离 Worktree 中运行。

## 冻结条件

- Case 集：既有 8 Case 加 `inventory_reservation_atomicity`、`cursor_revision_integrity`、`webhook_signature_canonicalization`；
- 每 Case 每 Version：3 次 Trial；
- Protocol：执行前写入各自 `protocol.json`，包括 Prompt Profile、采样配置、平台源码 Hash 与 Agent 源码 Hash；
- 运行时身份：每个已选 Attempt 的 `agent_source_hash` 必须等于冻结 Hash，否则报告降级为 `not_comparable`；
- Gate：沿用 `configs/default-gate.json`，不为新版本或新 Case 重写阈值；
- 统计：按 Case 聚类 Bootstrap，95% CI、2,000 次重采样。

## 执行与验收

真实调用必须另行确认。确认后依次执行：

```sh
set -a; source .env; set +a
make external-v4-1-benchmark-v2
make external-v4-1-benchmark-v2-gate
make audit-v4-1-benchmark-v2

make external-v3-negative-control-benchmark-v2
make external-v3-negative-control-benchmark-v2-gate
make audit-v3-negative-control-benchmark-v2
```

正向审计要求 66 条全部有效、11 Case 覆盖完整、V4.1 的 `verification_passed_policy` 后没有模型或工具调用，并且 Gate 为 `PROMOTE`。负向审计要求 66 条全部有效、11 Case 覆盖完整、每条负向 Trace 恰有两次冗余模型调用且其后没有工具调用，并且 Gate 为 `HOLD`。

两项实验均于 2026-08-14 完成：正向审计为 `PROMOTE`，负向审计为 `HOLD`。
正式结论见 [`EXPERIMENT_REPORT_EXTERNAL_V3_V4_1_BENCHMARK_V2.md`](./EXPERIMENT_REPORT_EXTERNAL_V3_V4_1_BENCHMARK_V2.md)
与 [`EXPERIMENT_REPORT_EXTERNAL_V3_NEGATIVE_CONTROL_BENCHMARK_V2.md`](./EXPERIMENT_REPORT_EXTERNAL_V3_NEGATIVE_CONTROL_BENCHMARK_V2.md)。

若未来复现实验发生模型失败、Trace 无效、协议不严格可比或 Gate 结论与预期不同，应保留 Artifact 并先分析原因；不能用旧 8 Case 结果补足新实验。
