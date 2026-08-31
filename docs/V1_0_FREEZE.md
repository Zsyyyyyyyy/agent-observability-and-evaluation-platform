# Regression Lab v1.x 契约冻结说明

## 定位

本文描述 v1.x 已采用的核心契约，并不表示项目已成为多租户生产服务。Regression Lab 仍定位为 framework-neutral Agent regression evaluation and observability platform for reproducible version comparison。

它负责把同一 Benchmark Case 上的 Agent 版本变化，收敛为可复现、可审计的
`Case × Trial × Version` 实验；不绑定任何 Agent 框架，也不将诊断结果变成自动根因推测。

## 冻结范围

以下契约自 v1.0 起按兼容接口维护。当前 v1.3 已使用这些版本字段和兼容测试；若必须修正设计，仍需要提供明确的迁移方案。

| 契约 | 冻结版本 | 稳定语义 |
|---|---:|---|
| Trace Schema | v1 | 通用 agent/workflow/llm/tool 等 Span、父子关系、Trace v0 读取兼容。 |
| Behavior Diff | v1 | Trace/Result Artifact 是事实来源；配对 Trial 差异与语义模式仅作 diagnostic，不参与 Gate。 |
| Adapter Capability | v2 | `available`、`supported_but_not_observed`、`unsupported` 三态；unsupported 不能伪装为 0。 |
| Experiment schema | metrics v3 | 冻结 Protocol、配对比较、Behavior Diff、Failure Attribution 和可比性字段的既有含义。 |
| Gate semantics | schema v4 | Gate 只基于既有确定性评测与可靠性规则；诊断层不改变晋级结论。 |
| Trial / Attempt semantics | Attempt schema v1 | Trial 是逻辑样本，Attempt 是不可变执行证据；选中 Attempt 是兼容投影，不能覆盖历史证据。 |

## 变更纪律

- 不随意修改上述核心数据结构、字段含义或统计口径。
- 新能力先记录在 [Roadmap](ROADMAP.md)，不直接进入稳定契约。
- 若确有必要修改冻结契约，必须发布新 schema/version，明确读取兼容与 Artifact 迁移策略，并保留旧版本回归测试。
- 只要 Capability 或 Trace 证据不足，就返回 unavailable；不得以默认值、推测或 UI 补零改变历史事实。

## v1.x 已验证的边界

- 内置 `react-agent`、通用 `external-command`、readonly replay 与故障探针；真实 LangGraph 示例通过 `external-command` 接入，不需要框架特判。
- Hierarchical Trace、跨版本 Behavior Diff、Span-level Failure Attribution、只读 Console 和 Capability Contract 已端到端验证。
- Gate、Sandbox、Evaluator、Trial/Attempt 仍保持独立于 Agent 框架的语义。

详细的版本后候选工作见 [Roadmap](ROADMAP.md)。
