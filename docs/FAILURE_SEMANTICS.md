# 失败语义与证据链

Regression Lab 不把所有非成功结果混为“失败”。Trial 的状态、Trace 和 Evaluator Evidence 共同解释失败来源。

## 归因与双可靠性视图

每个不通过 Trial 都会获得一个互斥的主归因，按以下优先级确定：`model` → `infrastructure` → `evidence` → `policy` → `agent`。这个顺序确保一个同时出现策略问题与模型网关错误的 Trial 被归因给真正阻断执行的外部失败，而不会在多个桶中重复计数。

- **Raw Reliability**：有效通过 / 全部 Trial。包括 Agent、模型、基础设施、证据链和策略失败；这是唯一用于 Release Gate 的可靠性口径。
- **Agent Quality**：有效通过 / 排除 `model` 与 `infrastructure` 后的 Trial。它只用于区分“Agent 本身的任务质量”和环境波动，不能用于放宽或覆盖 Gate。

`trace_incomplete`、路径/Diff/工具策略违规不会被排除，因为它们都是 Agent 交付证据或操作边界的一部分。

| 场景 | Trial 状态 | 可定位证据 | 当前验证 |
|---|---|---|---|
| 模型网关/鉴权/响应异常 | `model_failed` | `agent_exit_reason=model_error`、`model.call` error Span | `test_model_failure_is_persisted_as_a_distinct_non_passing_trial` |
| Trace 校验失败 | `trace_incomplete` | Trace 校验错误、Root Span 与 Trial Result | `test_invalid_trace_cannot_be_marked_completed_or_reused` |
| Agent 尝试修改禁止文件 | 工具调用被 `denied`；通常随后为 `agent_failed` | `tool.call` Span、未变化的 Worktree、Diff/Score | `test_forbidden_path_is_denied_before_test_can_be_modified` |
| 测试命令超时 | `timed_out` | Docker Sandbox 状态、测试 stderr、Root Span 状态 | `test_command_timeout_is_enforced_by_runner` |
| Docker 不可用 | `infra_failed` | Sandbox unavailable 错误与 Result | React Worker 的 `_run_test` 分支及 Sandbox 测试 |
| 路径或 Diff 不符合验收 | Trial 可完成，但 `evaluation.passed=false` | `path_policy` / `diff` Score 的 violating evidence | Evaluator 单元测试 |

## 执行时阻断与执行后评测

`react-agent` 对 `write_file` 和 `edit_file` 同时执行两道校验：

1. 工具调用时，根据 Manifest 的 Allowed/Forbidden Paths 拒绝不合法修改，防止 Agent 短暂篡改测试后再依赖测试结果。
2. Trial 结束后，`PathPolicyEvaluator` 再根据 Git Diff 检查最终产物，作为独立、可审计的第二道防线。

这两层都保留，是因为“拦住了请求”和“最终仓库没有违规改动”是不同的工程事实。

## Manifest 驱动的故障注入

`failure-probe` 是平台自测 Adapter，不接入模型、不代表真实 Agent。它通过三个 Manifest 生成受控的违规 Attempt：

- `failure-path-violation.yaml`：测试 `path_policy` 阻断禁止路径修改；
- `failure-unauthorized-tool.yaml`：测试 `tool_integrity` 阻断未授权工具调用；
- `failure-timeout.yaml`：测试 `timed_out` 状态与测试评分失败。

运行 `make failure-suite` 会在 Docker Sandbox 中执行三条 Probe。命令返回 0 的含义是“平台成功识别并阻断了这些预期失败”，不是 Probe 本身通过了任务。
