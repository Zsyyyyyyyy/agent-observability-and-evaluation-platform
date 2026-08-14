# External Agent Integration v1 — Acceptance Plan

> 状态：Proposed。所有用例通过后，才认为“真实 Agent 快速接入 MVP”完成。

## A. 契约与 SDK

### A1. 最小有效 Trace

给定一个使用 SDK 的外部 Agent，当它创建并关闭一个 `agent.run`、一个 `model.call` 和一个 `tool.call` 后：

- JSONL 每行是有效 JSON；
- Event Sequence 严格递增；
- 所有 Span 成对闭合；
- `validate_trace` 返回 `valid=true`；
- Trace Inspector 显示真实模型和工具名称。

### A2. 不完整 Trace 失败关闭

模拟 Agent 异常退出并留下未关闭 Span：

- Agent 进程可以结束；
- 平台将 Trial 标记为 `trace_incomplete`；
- Trial 不得计为有效通过；
- Console 能展示校验错误。

### A3. SDK 写入失败不劫持 Agent

使 Trace Path 不可写：

- SDK 不得用观测异常覆盖 Agent 原始异常或返回值；
- 平台最终因缺失 Trace 将 Trial 判为 `trace_incomplete` 或 `infra_failed`；
- Result 中保留可审计错误摘要。

### A4. 敏感信息不落盘

使用包含伪 API Key、Authorization Header 和长 Prompt 的测试输入：

- Trace、Result、SQLite 和 JSONL Audit 中均不存在伪密钥；
- 模型 Span 只包含模型名、计数、耗时和 Usage；
- 工具输出预览不超过 240 字符；
- 错误摘要不超过 500 字符。

### A5. 单写入者与 Usage 聚合

- 每个 Trial 只能初始化一个 Trace Writer，并从 `event_seq=1` 开始；
- 同一进程内的写入必须串行化，不接受多进程并发追加；
- `model_usage` 必须由平台从已校验的 `model.call` 事件聚合；
- 最终输出文件中的 Usage、Token 或模型覆盖字段必须被忽略。

## B. external-command Adapter

### B1. 平台身份注入

启动最小外部 Agent 命令：

- Agent 能读取全部必需 `REGRESSION_*` 环境变量；
- Agent 写入与平台分配值不同的 Trial ID、Trace ID、版本或 Adapter 时，Trace 校验失败；
- 根 Span 的 Trial ID 与平台预期一致。

### B2. 命令与输出边界

- Manifest 只接受 argv 数组，不接受 Shell 命令字符串；
- Adapter 使用非 Shell 方式启动进程；
- stdout/stderr 不作为业务 Result 解析；
- Agent 只能通过 `REGRESSION_AGENT_OUTPUT_PATH` 提交 `agent_response` 和 `agent_exit_reason`；
- 输出必须通过 UTF-8、JSON 类型和长度校验，并使用同目录原子替换；
- 输出缺失或非法时不得回退解析 stdout；
- 输出文件中的 Score、Gate 或身份覆盖字段必须被忽略。

### B3. 平台拥有评测证据

让 Agent 声称“测试通过”，但实际代码测试失败：

- 平台忽略 Agent 自报结论；
- `TestEvaluator` 失败；
- 最终 `evaluation.passed=false`。

### B4. 路径与工具策略

分别模拟禁止路径修改和未授权工具调用：

- Path Policy 或 Tool Integrity Score 失败；
- Evidence 指向具体文件或工具；
- 违规 Trial 不进入有效通过数。

### B5. 本地信任边界

- 未在 Manifest 中显式配置的命令不得运行；
- 文档必须明确 v1 只支持用户信任的本地 Agent；
- 测试证明外部命令不能通过 Result 文件覆盖平台的 Trial ID、Trace ID、Score 或 Gate；
- 不宣称 v1 能防止恶意本地进程读取 Worktree 以外的宿主机文件。
- 不宣称平台能发现所有未通过 SDK 埋点的工具调用；Promotion 示例必须完整埋点。

### B6. 进程故障语义

覆盖正常完成、非零退出、模型错误、超时和不可启动命令：

| 场景 | 预期状态 |
|---|---|
| Agent 正常结束且测试通过 | `completed` |
| Agent 正常结束但测试失败 | `agent_failed` |
| 明确模型调用错误 | `model_failed` |
| 超过 Trial Deadline | `timed_out` |
| 命令不存在或运行环境故障 | `infra_failed` |

## C. 真实示例 Agent

### C1. 单版本 Smoke

使用一个原生 Python 外部 Coding Agent 和一个确定性 Case：

- Agent 代码不导入内部 Adapter；
- 只通过 SDK 和环境契约接入；
- 完成真实模型调用、至少一个工具调用和代码修改；
- Result、Trace、Diff 与所有 Score 可在 Console 查看。

### C2. 无框架依赖

示例 Agent 只依赖 Python 标准库、OpenAI-compatible Client 和 Observer SDK，不依赖 LangChain、LangGraph 或 AutoGen。该限制仅针对官方示例，不限制真实用户 Agent。

## D. v1/v2 对比实验

### D1. 单变量对照

两个版本必须共享 Agent 代码、模型、参数、工具、Case 和预算，只允许 System Prompt 不同。实验元数据必须记录两个 `agent_profile`。

### D2. 重复实验

第一轮 MVP 使用至少 3 个 Case，每个版本每个 Case 执行 3 次，共至少 18 个真实 Trial：

- 不覆盖失败或无效 Artifact；
- 支持 `--resume`；
- 报告能计算通过率、模型失败率、工具调用、Token 和耗时差异。
- 报告能按 [Evaluation Metrics v2](EVALUATION_METRICS_V2.md) 计算 Pass@3、Flaky Case Rate、P50/P95 与逐 Case 配对差异；缺失数据必须明确标为不可计算。

### D3. Promotion Gate

- `experiment.json` 可由现有比较逻辑读取；
- `gate-report.json` 可由现有 Gate Policy 生成；
- Console 能并列展示 v1/v2；
- Gate 结论引用平台 Score，而不是 Agent 自报结果。

## E. 回归与交付

### E1. 现有能力不回归

- `react-agent` 和 `readonly-replay --dry-run` 保持可用；
- 现有 Trace Schema 和 Evaluator 测试全部通过；
- Console 仍为只读，不因接入功能获得写接口；
- Docker Sandbox 与 Failure Suite 继续通过。

### E2. 快速接入体验

一名不了解平台内部实现的开发者应能按照文档在 10 分钟内完成：

1. 配置一个外部命令；
2. 用 SDK 包裹模型和工具调用；
3. 运行一个 Trial；
4. 在 Console 中定位该 Trial 的 Trace 与 Diff。

### E3. 发布前 Gate

实现完成后必须先在本地完成以下检查，再允许推送：

```bash
make test
make docker-test
make failure-suite
make manifest-check
node --check web/app.js
git diff --check
```

此外必须使用一个全新临时目录执行 Quick Start，并人工检查 Console 页面、端口、公开截图和 README 命令。本地审核通过后仍须获得项目所有者明确确认，才可以提交、推送或发布。

## 实施顺序

1. SDK Event Writer 与单元测试；
2. `external-command` Adapter 与故障语义测试；
3. 最小外部 Agent 单版本 Smoke；
4. v1/v2 Prompt Profile；
5. 3 Case × 3 Trial 真实实验；
6. Console、Gate、文档和干净环境发布审计。
