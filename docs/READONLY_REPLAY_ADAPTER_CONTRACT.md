# Read-only Replay Adapter Contract

> 状态：Day 1 设计冻结草案  
> 被测对象：外部 `agent_entry.py` 的只读接入（本仓库不包含该源码，运行时通过 `--replay-source` 显式提供）
> 适配器实现位置：`adapters/readonly_replay/`
> 目标：在不改动外部 Agent 源码的前提下，将一次 Agent Run 转换为可追踪的 Trial

## 1. 审计结论

### 1.1 可复用的运行入口

legacy agent 的交互入口位于 `if __name__ == "__main__"`，但真正的 Agent 循环由以下函数承载：

```python
agent_loop(messages: list, context: dict) -> None
```

它会原地修改 `messages`，并在没有新的 `tool_use`、发生不可恢复异常或恢复次数耗尽时返回。因此 Adapter 不启动交互式 CLI，而是在独立 Worker 进程中：

1. 设置 Trial Worktree 为当前工作目录。
2. 加载 legacy agent 模块。
3. 创建单条用户消息和空 Context。
4. 调用 `agent_loop(messages, context)`。
5. 从消息历史、Trace Collector 和 Worktree 中生成 Trial Result。

### 1.2 导入前置条件

legacy agent 在模块导入阶段会读取环境和初始化全局状态：

- `load_dotenv(override=True)`。
- 创建 Anthropic Client。
- 读取必需的 `MODEL_ID`。
- 以 `Path.cwd()` 固定 `WORKDIR`。
- 创建 `.tasks`、`.worktrees` 等目录。
- 启动 Cron Scheduler 后台线程。

因此每个 Trial 必须使用新的 Worker 进程，不能在同一个 Python 进程中复用 legacy agent 模块。Adapter 必须在导入前完成：

- `MODEL_ID`、API Base URL 和必要凭证注入。
- Worktree 当前目录切换。
- Trial 专属的 `HOME`、`.tasks`、`.mailboxes` 等运行目录设置。
- Tool Policy 和 Trace Collector 初始化。

### 1.3 调用与工具边界

legacy agent 的主循环每轮执行以下流程：

```text
prepare_context
  → call_llm
  → response.stop_reason 处理
  → PreToolUse hooks
  → tool handler / background task
  → PostToolUse hooks
  → tool_result 写回 messages
```

适配器可利用的稳定边界：

- `call_llm(...)`：模型调用、重试、模型配置和响应耗时。
- `assemble_tool_pool()`：工具定义和 Handler 汇总。
- `BUILTIN_HANDLERS`：工具名称到执行函数的映射。
- `trigger_hooks("PreToolUse", ...)`：权限检查前的工具事件。
- `trigger_hooks("PostToolUse", ...)`：工具执行后的结果事件。
- `compact_history(...)`、`reactive_compact(...)`：上下文压缩事件。
- `agent_loop(...)`：Agent Run 根边界。

## 2. Adapter 原则

### 2.1 Baseline 与 Candidate

```text
agent_entry.py
        │
        ├── Baseline：原始模块，代码只读
        │
        └── Candidate：Regression 内的 Adapter Profile/版本配置
```

Baseline 不在运行过程中写入 legacy agent 源目录。Candidate 的行为变化必须通过 `study/Regression/` 内的 Profile、Wrapper 或独立实现表达，并记录到 AgentVersion。

### 2.2 单 Trial 单 Worker

每次 Trial 独立启动 Worker 进程，以避免以下全局状态跨实验污染：

- `messages` 和 `context`。
- `rounds_since_todo`。
- `mcp_clients`。
- `background_tasks`。
- `scheduled_jobs`。
- legacy agent 导入阶段创建的文件和线程。

Worker 退出后，Adapter 只从结构化 Result、Trace 文件和 Git Worktree 获取结果。

### 2.3 工具权限最小化

Coding Benchmark 初期只允许：

- `read_file`
- `write_file`
- `edit_file`
- `glob`
- `bash`（必须进入 Tool Sandbox）

以下工具默认禁止：

- `spawn_teammate`
- `connect_mcp`
- `schedule_cron`
- `create_worktree`
- `remove_worktree`
- `keep_worktree`
- `deploy` 类 MCP 工具

legacy agent 的 System Prompt 仍会列出完整工具目录，因此 Adapter 必须在实际 Tool Pool 和 PreToolUse Policy 两层同时限制，不能只依赖 Prompt。

## 3. Trace Contract

### 3.1 根 Span

每次 Worker Run 创建一个根 Span：

```text
agent.run
```

必填属性：

- `run.id`
- `trial.id`
- `agent.version`
- `case.id`
- `case.version`
- `base.commit`
- `worktree.path_hash`
- `runtime.image_digest`
- `model.name`
- `adapter.version`

### 3.2 模型 Span

每次 `call_llm` 创建：

```text
agent.run
└── model.call
```

记录：

- 当前模型和回退模型。
- 请求开始、结束和耗时。
- 输入消息数量及估算字节数。
- 工具 Schema Hash。
- `stop_reason`。
- 输入/输出 Token（Provider 提供时）。
- 重试次数。
- 错误类型和脱敏后的错误信息。

不要默认保存完整 Prompt；保存 Prompt Hash 和脱敏后的摘要，完整内容进入受控 Artifact。

### 3.3 工具 Span

每次工具调用创建：

```text
agent.run
└── tool.call
```

记录：

- `tool.name`
- `tool_use_id`
- 参数 Hash 和脱敏摘要。
- Permission 结果：`allowed`、`denied`、`needs_approval`。
- 容器 ID/镜像 Digest（适用于 `bash`）。
- 开始、结束和耗时。
- 退出码和结果 Artifact Hash。
- 是否后台执行。

### 3.4 事件 Span

以下事件作为 Event 或独立 Span：

- `context.compact`
- `retry`
- `timeout`
- `error`
- `agent.stop`

事件必须带单调递增的 `event.seq`。Span 未正常闭合时，Trial 不能标记为 `completed`，而应标记为 `incomplete`。

## 4. 工具执行策略

legacy agent 原始 `run_bash` 使用宿主机 `subprocess.run(..., shell=True)`。这不能直接用于回归平台执行不可信模型输出。

Adapter 必须在 Worker 内替换工具 Handler：

```text
原始 legacy agent Tool Pool
        ↓
Regression Tool Policy
        ↓
Tool Proxy
        ↓
Docker Sandbox
```

具体要求：

- `bash` 只向 Docker Runner 发送结构化命令。
- `read/write/edit/glob` 只允许访问当前 Worktree。
- 路径先规范化，再检查是否位于 Worktree 内。
- 容器默认无网络。
- 容器根文件系统只读。
- Worktree 是唯一读写挂载点。
- 超时、CPU、内存和 PID 由 Runner 强制执行。
- Tool Proxy 记录命令摘要，不在 Trace 中存放未经脱敏的完整秘密。

## 5. Worker 输入输出

### 5.1 输入

Worker 接收一个 JSON 文件或 JSON stdin：

```json
{
  "trial_id": "trial_smoke_001",
  "agent_version": "legacy agent-baseline-v1",
  "case_id": "smoke_calculator_empty_input",
  "prompt": "修复空输入导致的计算器异常，并运行测试。",
  "worktree": "/absolute/path/to/worktree",
  "tool_policy": "coding-default",
  "trace_output": "/absolute/path/to/trace.jsonl",
  "result_output": "/absolute/path/to/result.json"
}
```

### 5.2 输出

Worker 必须生成结构化 Result，即使 Agent 失败：

```json
{
  "trial_id": "trial_smoke_001",
  "status": "completed",
  "agent_exit_reason": "stop_without_tool_use",
  "trace_id": "trace_...",
  "message_count": 5,
  "changed_files": ["calculator.py"],
  "diff_artifact": "sha256:...",
  "stdout_artifact": "sha256:...",
  "stderr_artifact": "sha256:...",
  "test_exit_code": 0,
  "duration_ms": 12345,
  "error": null
}
```

允许的 `status`：

- `completed`
- `agent_failed`
- `timed_out`
- `infra_failed`
- `incomplete`

## 6. 适配器验收标准

### Day 1 设计验收

- [x] 已确认 legacy agent 存在可调用的 `agent_loop`。
- [x] 已确认 legacy agent 不是纯黑盒，可从 `call_llm`、Tool Pool 和 Hook 边界包装。
- [x] 已确认模块导入有全局副作用，因此采用单 Trial 单 Worker。
- [x] 已识别原始 `run_bash` 不能直接用于不可信 Agent 输出。
- [x] 已定义 Tool Allowlist 和禁止工具。
- [x] 已定义 Worker 输入输出和 Trace Contract。

### Day 2–3 运行验收

- [ ] Worker 能在临时 Worktree 中加载 legacy agent。
- [ ] Worker 能通过 Adapter 注入一个固定 Prompt。
- [ ] Worker 能完成至少一次模型调用。
- [ ] Worker 能捕获至少一次工具调用或明确记录无工具调用。
- [ ] Worker 能生成结构化 Result。
- [ ] Worker 能收集测试退出码和 Git Diff。
- [ ] Phoenix 能显示完整 `agent.run` Trace。
- [ ] legacy agent 原始文件没有修改。

## 7. 已知限制

- legacy agent 的全局模块状态使进程内多 Trial 复用不安全，因此 v0.1 不支持进程内复用。
- legacy agent 的 System Prompt 会展示比 Coding Case 实际允许更多的工具，需要 Adapter 层额外限制。
- legacy agent 原始实现不是沙箱；安全边界由 Regression Tool Proxy 和 Docker Runner 提供。
- 后台任务、MCP、Cron 和 Teammate 在 Coding Benchmark 中默认禁用，不作为 v0.1 评测范围。
- 如果某些内部事件无法通过外部包装捕获，必须在报告中标记 Trace Coverage，而不是伪造完整 Trace。
