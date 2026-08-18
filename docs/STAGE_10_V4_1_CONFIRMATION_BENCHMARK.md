# 阶段 10：V3 → V4.1 完整确认 Benchmark

## 决策目标

阶段 9 的 3 Case 预实验已验证“成功即结束”可减少成功后的冗余推理，但覆盖不足以形成广泛结论。本阶段在独立 Artifact 中验证：V4.1 是否能在 **8 个异质 Case × 每版本 3 次 = 48 条真实 Trial** 中保持修复质量，并稳定降低工具调用、Token 与延迟。

## 实验设计

| 角色 | 版本 / Profile |
| --- | --- |
| Baseline | `external-openai-v3` / `targeted-context-verify-v3` |
| Candidate | `external-openai-v4.1` / `bounded-success-stop-verify-v4-1` |

固定模型、采样参数、外部 Agent 实现、工具白名单、Sandbox、Evaluator、预算规则与每 Case 的 fixture；唯一干预是 V4.1 对成功验证的确定性结束条件。运行器使用 `schedule_seed=20260814` 作配对、交错调度，并冻结新的 Protocol schema v2 / Prompt Hash。

## Case 覆盖

| Case | 风险类型 |
| --- | --- |
| `dependency_cycle_detection` | 递归状态与领域异常 |
| `batch_partial_failure_isolation` | 批处理局部失败与顺序保持 |
| `profile_v1_migration` | 跨版本迁移与可变默认值 |
| `cache_expiry_boundary` | 时间边界与注入时钟 |
| `config_inheritance_precedence` | 多层继承与覆盖优先级 |
| `permission_deny_precedence` | glob 策略与 deny 优先级 |
| `safe_slug_punctuation` | 文本规范化与标点边界 |
| `parse_port_blank_default` | 配置默认值与输入边界 |

## 执行

```bash
cd "$(git rev-parse --show-toplevel)"
set -a; source .env; set +a
make external-v4-1-benchmark
```

输出只写入 `.runtime/external-openai-v3-v4-1-benchmark/`，绝不覆盖阶段 9 的预实验。该命令会产生真实模型调用，必须在单独确认后执行。

## 验收与解释边界

1. Protocol 必须是 `strict`，8 个 Case × 3 Trial × 2 版本必须完整；密钥不得进入 Artifact。
2. V4.1 的成功 Trial 应为 `verification_passed`，并且策略终止事件后不应存在新的 `model.call` / `tool.call`。
3. Gate 必须无正确性、可靠性、Trace、路径策略、差异策略或成本硬失败。
4. `model_failed` / `infra_failed` 将按外部失败单独归因；受污染配对不用于性能统计，也不自动重跑。
5. 仅当 Case 聚类 Bootstrap 的 95% 区间和 Case 方向一致时，才描述效率改善；即使 Gate 通过，也应报告覆盖和置信区间，而非只报均值。

## 离线验收

真实实验完成后，可使用以下命令重复检查现有 Artifact，不会调用模型：

```bash
make audit-v4-1-benchmark
```

该审计会校验 strict Protocol、8 Case × 3 Trial 的双版本覆盖、Gate 结论、Bootstrap 统计证据，以及 V4.1 每条成功 Trace 在 `verification_passed_policy` 之后没有新的 `model.call` / `tool.call`。
