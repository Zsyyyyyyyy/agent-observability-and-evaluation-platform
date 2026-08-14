# Agent Adapter Contract

Runner、Sandbox、Trace 和 Evaluator 不依赖某一个 Agent 框架。每个 Adapter 都是一个独立 Worker 进程，由 `regression_lab.adapters` 注册，并接受同一份 Trial Spec。

## Worker 入口

```text
python adapters/<adapter>/worker.py --input /absolute/path/trial-input.json
```

Worker 必须把最终结果写入 `result_output`，并在退出前关闭所有 Trace Span。退出码为 `0` 只表示 Worker 正常完成；Trial 是否通过由 `result.status` 和 Evaluator 决定。

## 输入边界

Runner 负责创建 Worktree、初始化 Git、构造 Sandbox、设置总 Deadline。Adapter 只能在 `spec.worktree` 内操作，并必须遵守：

- `allowed_tools` / `denied_tools`
- `allowed_paths` / `forbidden_paths`
- `budget`
- `sandbox`
- `trace_output` 与 `result_output`

关键身份字段为 `adapter_id`、`agent_version`、`trial_id` 与 `case_id`。它们会进入 Trace、Result 和 Resume 指纹。

## 输出职责

Adapter 负责 Agent 执行过程；平台仍负责测试、Git Evidence、Trace Validation、Evaluator 与 Store 持久化。最低 Result 字段：

```json
{
  "trial_id": "case_trial_001",
  "adapter_id": "react-agent",
  "agent_version": "react-v1",
  "status": "completed",
  "trace_id": "trace_xxx",
  "agent_response": "..."
}
```

## 当前 Adapter

`readonly-replay` 是确定性演示实现：复用外部只读 legacy agent Loop，但以 Replay Client 替代真实模型。它不包含 legacy agent 源码；执行时必须通过 `--replay-source /path/to/agent_entry.py` 显式提供来源。

`react-agent` 是最小真实 ReAct 实现。它使用 OpenAI-compatible Chat Completions Function Calling，运行时只从环境变量读取 `AGENT_API_KEY`、`AGENT_MODEL`，可选读取 `AGENT_BASE_URL`（默认 OpenAI `/v1` 地址）。密钥不会写入 Manifest、Trace、Result 或 Store。

`external-command` 是框架无关的本地进程 Adapter。Runner 仅接受 JSON argv 数组，并通过 `shell=false` 启动用户明确配置的命令。它把平台分配的 Trial、Trace 与 Worktree 身份以 `REGRESSION_*` 环境变量传入；外部 Agent 只能通过受控输出文件提交 `agent_response` 和 `agent_exit_reason`。测试、Git Evidence、Trace Validation、Score 与 Gate 均由平台独立完成。详细事件字段见 [External Agent Integration Contract](EXTERNAL_AGENT_INTEGRATION_CONTRACT.md)。
