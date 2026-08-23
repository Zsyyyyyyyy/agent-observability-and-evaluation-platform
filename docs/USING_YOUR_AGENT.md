# 使用自己的项目和 Agent

Regression Lab 评测的是“同一个项目任务上，两个 Agent 版本的行为和结果差异”。它不会直接修改用户的原始项目，而是从 Benchmark Fixture 创建独立 Worktree，在其中运行外部 Agent、测试和 Git Evidence。

## 1. 准备一个 Fixture

把待修复项目的基线版本放到 Regression Lab 的 Fixture 目录，例如：

```text
fixtures/my-project/
├── src/                 # 有意保留缺陷的基线源码
├── tests/               # 验收测试
└── ...
```

Fixture 应该是一个可独立测试的项目。Manifest 中的 `fixture.path` 指向它，平台每次 Trial 都会复制它，不会修改原目录。

## 2. 写一个 Case Manifest

在 `benchmarks/my-case.yaml` 描述任务、测试命令、允许修改的路径和工具策略。可以从现有 Manifest 复制后修改，最小结构如下：

```yaml
schema_version: 1
status: ready
id: my-project-case
version: 1
title: 修复一个明确的问题

fixture:
  path: fixtures/my-project
  language: python
  test_command: python -m unittest discover -s tests -v

task:
  prompt: 只能修改 src/，修复描述清晰的问题并保持已有行为不变。
  allowed_paths: [src/**]
  forbidden_paths: [tests/**]

execution:
  timeout_seconds: 180
  max_tokens: 12000
  max_tool_calls: 20
  max_retries: 1
  trials: 3
  network: none

tool_policy:
  allow: [read_file, write_file, edit_file, glob, bash]
  deny: [spawn_teammate, connect_mcp, create_worktree]

evaluators:
  required: [test, path_policy, diff, tool_integrity, budget, trace_completeness]

acceptance:
  must: [test_exit_code == 0, forbidden_path_changes == 0, result_status == completed]
```

先验证 Manifest：

```bash
PYTHONPATH=src:. python3.11 scripts/run_benchmark.py \
  --manifest benchmarks/my-case.yaml --dry-run
```

## 3. 让 Agent 接入 Observer SDK

外部 Agent 以普通本地命令启动。它通过环境变量取得当前 Trial 身份、Worktree、Trace 输出路径和 Agent 输出路径：

```python
from regression_lab.sdk import AgentObserver

observer = AgentObserver.from_environment()
with observer.run():
    with observer.model_call(model="your-model") as call:
        call.record_usage({
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
        })
    with observer.tool_call("read_file"):
        ...
    with observer.tool_call("edit_file"):
        ...

AgentObserver.write_agent_output("completed", "workflow_completed")
```

Agent 应该使用 `REGRESSION_WORKTREE` 作为项目目录，不要修改原始 Fixture。完整字段和输出限制见 [External Agent Integration Contract](EXTERNAL_AGENT_INTEGRATION_CONTRACT.md)。

一次 Experiment 接收一个固定的 `--external-command`。如果两个版本使用不同实现，应让这个入口根据 `REGRESSION_AGENT_VERSION` 分发到对应版本；也可以让同一入口内部选择不同策略。这样平台能把版本身份和来源 Hash 固定在同一份 Protocol 中。

## 4. 比较两个 Agent 版本

也可以先启动 Run Studio，默认只需填写项目名、Agent 名、两个版本号、Python 解释器和两个 Agent 入口文件；平台会生成内部 AgentSpec 快照并在 Artifact 中冻结。无需手写 YAML。已有 AgentSpec YAML/JSON 的用户仍可切换到高级入口复用它们。随后勾选 Benchmark Case，并选择 Docker 或明确确认的可信主机执行：

```bash
make studio
```

打开 `http://127.0.0.1:8764`。Run Studio 只会执行通过 AgentSpec 和 Manifest 校验的固定 Experiment 命令；完成后会提供对应只读 Console 链接。命令行仍适合 CI 和自动化。

### 本地部署边界

Run Studio 是单用户、本地控制面，固定只监听 `127.0.0.1`，不提供网络暴露或多用户认证。它能够启动 Agent 进程，因此不能作为共享服务部署；如需服务化，应先补齐身份认证、租户级 Artifact/Secret 隔离、任务队列、资源配额和独立执行沙箱。

使用绝对路径启动 Agent，避免 Worker 在临时 Worktree 中运行时找不到入口文件：

```bash
PYTHONPATH=src:. python3.11 scripts/run_experiment.py \
  --adapter external-command \
  --external-command '["/absolute/path/to/.venv/bin/python", "/absolute/path/to/my_agent.py"]' \
  --agents baseline:my-agent-v1,candidate:my-agent-v2 \
  --project-id my-project \
  --trials 3 \
  --manifest benchmarks/my-case.yaml \
  --output-dir .runtime/my-project-v1-v2 \
  --unsafe-trusted-host
```

`project_id` 是被测项目的稳定身份，而不是用户名或 Agent 版本。新 Experiment 会将版本谱系写到 `.runtime/projects/my-project/evolution-catalog.json`，因此同一个 Agent 在不同项目上的历史不会混在一起。旧的无 `project_id` Artifact 仍可读取，但 Console 会标记为 `Legacy Catalog (no project identity)`。

`run_experiment` 还会在正式执行前调用入口的 `--describe-protocol`，确认每个版本的 Prompt Profile 与哈希。可以参考 [external_openai_agent.py](../examples/external_openai_agent.py) 中的协议描述实现；只运行单个 Trial 做接入调试时，可以先使用 `run_benchmark.py`，不需要这一步。

默认使用 Docker 执行平台测试；`--unsafe-trusted-host` 只适合明确可信的本地 Agent 命令。它不会把 Agent 进程变成安全沙箱。

如果 Agent 能输出工作流 Span，应显式声明 Capability；不支持的能力必须写成 `false`：

把下面这个参数追加到上一条命令即可：

```text
--adapter-capabilities '{"schema_version":2,"trace":true,"hierarchical_trace":true,"model_usage":true,"tool_trace":true,"tool_semantics":true,"test_trace":false,"context_trace":false,"workflow_trace":true,"mcp_trace":false}'
```

不要把没有 Trace 证据的指标当成 0。平台会区分 `available`、`supported_but_not_observed` 和 `unsupported`。

## 5. 查看结果

实验完成后，事实保存在 `.runtime/my-project-v1-v2/`；版本谱系索引独立保存在 `.runtime/projects/my-project/evolution-catalog.json`：

```text
experiment.json       # Baseline/Candidate 聚合、Behavior Diff、统计
baseline/.../result.json
candidate/.../result.json
.../trace.jsonl       # 每个 Trial 的层级 Trace
```

启动只读 Console：

```bash
make console \
  RUNTIME=.runtime/my-project-v1-v2 \
  CONSOLE_PORT=8767
```

Console 会展示 Experiment、Case、Trial、Behavior Diff、Failure Attribution 和原始 Trace；它只读 Artifact，不重新执行 Agent，也不改变 Gate。

## 6. 运行自己的版本实验时要保持什么不变

为了让差异可解释，Baseline 与 Candidate 应只改变一个明确策略，例如“减少一次重复读取”。同一轮实验应保持 Case、Fixture、测试命令、工具策略、模型配置和 Trial 数量一致。

当前 v0.2 已对 Trace、Behavior Diff、Capability、Experiment、Gate 和 Trial/Attempt 建立版本化契约与兼容测试，但尚未宣称生产级 v1.0 稳定性。新增能力先看 [Roadmap](ROADMAP.md)，不要直接修改既有 Artifact 结构。
