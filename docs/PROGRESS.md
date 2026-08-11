# 执行进度

## 2026-08-11：Day 13 交付封装与可复现验证

### 已完成

- [x] 新增项目根 README，说明安全边界、架构、真实 Agent 接入和实验结论。
- [x] 新增 Makefile：核心测试、显式 Docker 集成测试、Manifest 校验、真实 Smoke 与只读控制台均有固定入口。
- [x] 新增独立仓库可用的 GitHub Actions 校验工作流；CI 不调用真实模型、不需要密钥。
- [x] 新增 `.env.example`，明确环境变量由终端加载且不会写入 Artifact。
- [x] 新增 5 分钟演示脚本与可直接使用的简历/面试表述。

### 下一步

以 4 个代表性 Case × 3 Trial 重跑真实 v1/v2 对照，报告中位数与方差；同时补充预期失败 Case，验证 `model_failed`、路径违规与超时等失败语义。

## 2026-08-11：Day 14 失败语义与路径执行时拦截

### 已完成

- [x] `react-agent` 的 `write_file` / `edit_file` 在工具调用时强制执行 Manifest 的 Allowed/Forbidden Paths；不再仅在 Trial 结束后发现违规。
- [x] 保留 Git Diff 的 PathPolicyEvaluator，形成执行时阻断与执行后审计两道防线。
- [x] 新增端到端测试：禁止修改测试文件时，Worktree 不变、工具 Span 为 `denied`、Trial 不会通过评测。
- [x] 新增端到端测试：模型错误稳定写为 `model_failed`，Trace 仍完整、评测不通过。
- [x] 汇总 Docker 超时、基础设施失败、策略违规等失败状态与证据链文档。

### 验证结果

```text
targeted failure semantics: 5 passed
full unit suite: 45 passed (3 Docker integration tests skipped by default)
```

### 待用户确认的下一步

运行 4 个代表性 Case × 3 Trial × v1/v2 的真实模型重复实验（24 次外部模型调用），然后输出中位数、离散度与版本晋级建议。

## 2026-08-11：Day 15 重复实验与数据完整性修复

### 已完成

- [x] 完成 4 Case × 3 Trial × v1/v2 的真实重复实验；两个版本均为 11/12 完成、评测通过且 Trace 合法。
- [x] 发现并隔离一次并发写入造成的 3 条污染 Trace；原 Artifact 归档后仅重跑受影响 Trial，不混入统计。
- [x] `react-agent` 的 Trace 校验失败现在进入 `trace_incomplete`，不会被误写为可恢复的 `completed`。
- [x] Runner 新增 `--rerun-invalid`：归档无效 Artifact 后才重跑，避免静默覆盖证据。
- [x] Experiment Runner 新增 `--report-only`：从已有 Summary 重建报告，不读取密钥、不调用模型。
- [x] 输出重复实验报告，包含成功率、中位数、标准差、模型超时样本与克制的晋级建议。

### 验证结果

```text
full unit suite: 46 passed (3 Docker integration tests skipped by default)
repeated experiment: v1 11/12, v2 11/12 valid passing trials
```

### 下一步

扩展到剩余 4 个 Case；将 Provider Timeout 独立统计为可靠性指标，并新增 Manifest 驱动的失败基准后，形成更完整的版本晋级 Gate。

## 2026-08-11：Day 16 全量核心集重复实验

### 已完成

- [x] 扩展至完整 8 Case × 3 Trial × v1/v2，共 48 条活跃真实 Trial。
- [x] v1 为 22/24（91.7%）有效通过；v2 为 23/24（95.8%）有效通过。
- [x] 两版本 `model_failed_rate` 均为 4.17%，`trace_incomplete_rate` 与 `infra_failed_rate` 均为 0。
- [x] 识别 v1 在端口解析任务的一次工具预算超限；v2 在该 Case 为 3/3。
- [x] 输出全量报告，区分全部尝试的 Gate 口径与成功样本的成本口径，不将二者混为单一结论。

### 下一步

实现可执行的版本晋级 Gate，并新增 Manifest 驱动的预期失败基准，把路径违规、工具拒绝与超时纳入回归验证。

## 2026-08-11：Day 17 可执行版本晋级 Gate

### 已完成

- [x] 实现实验 Gate：完成率、评测通过率、模型失败率、Trace/基础设施失败率、耗时、工具调用和 Token 均可配置判定。
- [x] 新增默认策略：正确性与可靠性 fail-closed；工具/Token 成本允许最多 10% 的统计波动。
- [x] 新增 `scripts/evaluate_gate.py`、`make gate` 与 `gate-report.json`，全程只读取已有报告，不执行 Agent。
- [x] 用完整 48 条真实 Trial 回放：v2 的 8 条 Gate 规则全部通过，判定为可晋级候选版本。

### 验证结果

```text
unit suite: 50 passed (3 Docker integration tests skipped by default)
gate replay: passed (8/8 rules)
```

### 下一步

新增 Manifest 驱动的预期失败基准，覆盖路径违规、未授权工具和超时，并验证 Gate 对失败语义的阻断能力。

## 2026-08-11：Day 18 Manifest 驱动的预期失败基准

### 已完成

- [x] 新增仅用于平台自测的 `failure-probe` Adapter；不连接模型、不会产生费用。
- [x] 新增路径违规、未授权工具、超时三个 Failure Manifest。
- [x] 验证每条 Probe 的 Trace 合法，且分别由 `path_policy`、`tool_integrity`、`test` 评分器阻断。
- [x] 新增 `make failure-suite` 与 CI Docker 步骤：命令成功代表平台识别了预期失败。

### 下一步

将 Gate 结果、Failure Suite 与 Console 展示收拢为最终演示流程，并准备独立发布仓库的提交材料。

## 2026-08-11：Day 19 面向版本决策的 Console

### 已完成

- [x] Console 默认首页改为发布决策视图，直接展示 Gate、通过率、耗时、Token 与可靠性变化。
- [x] 新增 Case Comparison Matrix：同一 Case 的 v1/v2 三次 Trial 并列，展示通过数、中位耗时、Token、工具调用和失败状态。
- [x] 新增 Paired Inspection：点击 Case 后左右并列展示两版本全部 Trial，再钻取单条 Trace/Diff。
- [x] 保留可筛选 Raw Trial Triage，作为调试而非首页主视图。
- [x] Dashboard API 增加 `case_id` 与 Gate 读取接口，并排除归档 Attempt。

### 验证结果

```text
unit suite: 52 passed (3 Docker integration tests skipped by default)
local console: 48 Trial / 8 Case / 8 Gate rules loaded
paired inspection: parse-port v1/v2 and budget_exceeded Trace drill-down verified
browser console errors: none
```

## 2026-08-11：Day 20 独立发布素材

### 已完成

- [x] 捕获 Release Desk Gate 总览与 v1/v2 并列 Case 对比两张真实 Console 截图。
- [x] README 已嵌入截图，并保持数据口径与全量实验报告一致。
- [x] 新增独立发布检查清单：测试、Artifact 安全、GitHub 发布和发布后验证。
- [x] 新增 GitHub 首页、Topics、演示视频和简历链接旁可直接复用的文案素材。

### 下一步

按发布清单在 `study/Regression` 初始化独立 Git 仓库、检查忽略文件后提交并推送；随后录制 3–5 分钟演示视频。

## 2026-08-10：Day 1 只读接入审计

### 已完成

- [x] 定位 s20 主入口和可调用的 `agent_loop(messages, context)`。
- [x] 确认 s20 可通过模块级函数边界接入，不必启动交互式 CLI。
- [x] 确认模型调用边界：`call_llm(...)`。
- [x] 确认工具边界：`assemble_tool_pool()`、`BUILTIN_HANDLERS` 和 Pre/Post Tool Hooks。
- [x] 确认上下文压缩边界：`compact_history(...)`、`reactive_compact(...)`。
- [x] 识别导入副作用和模块级全局状态。
- [x] 确认原始 `run_bash` 使用宿主机 Shell，不能直接作为安全执行层。
- [x] 冻结单 Trial 单 Worker 进程方案。
- [x] 冻结工具 Allowlist/denylist 初稿。
- [x] 冻结 Worker 输入输出和 Trace Contract 初稿。
- [x] 设计首个 Smoke Case。

### 今日产物

- [S20 Adapter Contract](./S20_ADAPTER_CONTRACT.md)
- [ADR-001：s20 Worker 进程](./ADR-001-s20-worker-process.md)
- [Smoke Case Design](../benchmarks/smoke-case-design.yaml)

### 当前结论

适配可行，但不能直接调用 s20 原始 `run_bash`。下一步先实现最小 Worker 和 Tool Policy，在一个 Fixture 上完成模型调用、工具调用、测试结果、Git Diff 和结构化 Result 的闭环。

## 2026-08-10：Day 2 Worker Smoke Harness

### 已完成

- [x] 实现依赖无关的 JSONL `TraceCollector`。
- [x] 实现单 Trial s20 Worker。
- [x] 使用独立 Worker 进程隔离 s20 模块级状态。
- [x] 加入 Replay Client，支持 read → edit → final 三轮确定性响应。
- [x] 包装 `call_llm` 为 `model.call` Trace。
- [x] 包装工具 Handler 为 `tool.call` Trace。
- [x] 记录 `permission.check` 和 `context.compact` 事件边界。
- [x] 限制初始工具池为 Coding Allowlist。
- [x] 对 `bash` 默认拒绝，避免在 Docker Runner 完成前调用宿主机 Shell。
- [x] 创建计算器空输入 Smoke Fixture。
- [x] 创建临时 Git 仓库和 Worktree，运行 Agent、测试和 Git Diff。
- [x] 验证结构化 Result、Trace Summary 和退出码。

### 验证结果

```text
status: completed
test_exit_code: 0
changed_files: src/calculator.py
trace_status: complete
trace_spans: 6
trace_events: 14
observed: agent.run / model.call / permission.check / tool.call
```

执行命令：

```bash
cd study/Regression
python3.11 scripts/run_smoke.py
```

### 当前限制

- 模型响应使用 Replay，不产生真实 API 请求。
- `bash` 暂时拒绝，Docker Tool Sandbox 尚未接入。
- 测试命令 Day 2 在受控宿主机子进程执行，Day 3 迁移到容器。
- Phoenix Exporter 尚未接入，当前先保存依赖无关 JSONL Trace。

### 下一步唯一优先任务

实现 Day 3 的 Tool Sandbox Harness：将 Smoke Case 的测试命令和 `bash` 工具从宿主机执行迁移到 Docker，并保留当前 Result/Trace 契约。

## 2026-08-11：Day 3 Docker Tool Sandbox 验收

### 已完成

- [x] 新增 `DockerSandbox`，统一封装 Docker argv、Worktree 挂载和资源限制。
- [x] 默认启用 `network=none`、`--cap-drop ALL`、只读根文件系统、无特权、CPU/内存/PID 限制和受限 `/tmp`。
- [x] 将 s20 的 `bash` Handler 接入 Sandbox；未配置 Sandbox 时继续明确拒绝。
- [x] 将 Trial 测试命令接入 Sandbox，并保留宿主机回退模式用于本地开发。
- [x] 增加 Sandbox 命令构造和失败状态单元测试。
- [x] 修复 Docker `--mount` 参数格式，并完成真实容器 Smoke。
- [x] 增加超时、Worktree 路径校验和真实 `bash` Replay 覆盖。

### 验证结果

```text
unit tests: 5 passed
host replay smoke: passed (6 spans, 14 events)
docker smoke: passed (6 spans, 14 events)
docker bash smoke: passed (8 spans, 19 events)
```

Docker Smoke 已在 `python:3.11-slim` 容器中通过。首次运行自动拉取镜像，测试命令在容器内执行并返回 `test_exit_code=0`：

```bash
cd study/Regression
python3.11 scripts/run_smoke.py --docker
```

### 下一步

超时和 Worktree 路径边界已有单元覆盖；下一步补充网络/资源限制的容器集成断言，随后进入 Day 4 的 Trace Schema 校验与 SQLite/JSONL Run Store。

## 2026-08-11：Day 4 Trace Schema 与 Run Store

### 已完成

- [x] 定义 JSONL Trace 的最小公共字段和 Span 生命周期规则。
- [x] 实现 Trace JSONL 校验器，检查 JSON、Trace ID、事件顺序和 Span 闭合。
- [x] 实现 SQLite + JSONL Run Store，支持 Trial upsert、按状态查询和审计追加。
- [x] Worker 在写 Result 前执行 Trace 校验并写入 Run Store。
- [x] Docker + bash Smoke 验证 Result、Trace Validation、SQLite 和 JSONL 全链路。
- [x] 增加并运行 Docker 网络隔离、只读根文件系统、`/tmp` 和超时集成测试。

### 验证结果

```text
unit tests: 10 passed
docker bash smoke: passed
trace_validation.valid: true
sqlite trials: 1
jsonl records: 1
docker integration: 3 passed
```

详细约定见 [TRACE_SCHEMA.md](./TRACE_SCHEMA.md)。

### 下一步

开始 Day 5 的评测器接口和首个基线评分器。

## 2026-08-11：Day 5 基线评测器

### 已完成

- [x] 定义统一 `Score` 输出：实际值、期望值、结论、消息和 Evidence。
- [x] 实现 `TestEvaluator`，记录退出码和测试数量摘要。
- [x] 实现 `PathPolicyEvaluator`，输出具体违规文件。
- [x] 实现 `TraceCompletenessEvaluator`，对接 Trace Schema 校验结果。
- [x] 实现 Baseline Evaluator 聚合器。
- [x] 将 Score 写入 Trial Result 和 SQLite `scores` 表。
- [x] 实现 `DiffEvaluator`、`ToolIntegrityEvaluator` 和 `BudgetEvaluator`。
- [x] 提供 `scripts/evaluate_trial.py`，支持重新评测已有 Result 并持久化 Score。

### 验证结果

```text
unit tests: 16 total (13 passed, 3 Docker integration tests skipped by default)
docker + bash smoke: passed
baseline scores: 6 evaluators = passed
single Trial evaluate CLI: passed
```

详细约定见 [EVALUATOR_SCHEMA.md](./EVALUATOR_SCHEMA.md)。

### 下一步

下一步开始 Day 6：建立 Benchmark Case Manifest 校验和多 Case 执行入口。

## 2026-08-11：Day 6 Benchmark Manifest 与多 Trial Runner

### 已完成

- [x] 实现 Manifest 加载，支持 JSON 和无 PyYAML 依赖的本地 YAML 子集。
- [x] 校验 Fixture、Prompt、Allowed/Forbidden Paths、预算、网络模式、工具策略和验收字段。
- [x] 将一个 Case 展开为稳定的 `job_id` 和 Case × Trial 任务列表。
- [x] 实现 `scripts/run_benchmark.py`，为每个 Trial 创建独立 Worktree、执行 Worker、收集 Result/Trace/Diff/Score。
- [x] 增加 `--dry-run` 校验/展开模式和 `--docker --bash` 实际执行模式。

### 验证结果

```text
manifest validation: passed
dry-run expansion: 2 jobs
docker benchmark run: 2/2 completed
sqlite trials: 2
sqlite scores: 12
```

详细约定见 [MANIFEST_SCHEMA.md](./MANIFEST_SCHEMA.md)。

### 下一步

扩展到多个不同类型 Case，并实现 Experiment 汇总、Baseline/Candidate 对比和失败恢复。

## 2026-08-11：Day 7 Experiment 对比与恢复

### 已完成

- [x] 实现 Baseline/Candidate Agent Version 的任务展开。
- [x] 扩展 Runner 支持 `--agent-version` 和 `--resume`。
- [x] 生成完成率、评测通过率、测试通过率、耗时、工具调用和 Diff 大小对比。
- [x] 已完成 Trial 在恢复运行中被复用，不重复执行。
- [x] 新增 `scripts/run_experiment.py` 和 Experiment 对比报告。

### 验证结果

```text
agents: baseline / candidate
trials per agent: 1
completed: 2/2
evaluation passed: 2/2
resume: reused completed jobs
```

详细约定见 [EXPERIMENT_SCHEMA.md](./EXPERIMENT_SCHEMA.md)。

### 下一步

补充第二个不同类型 Benchmark Case，开始验证多 Case 聚合和回归检测。

## 2026-08-11：Day 8 多 Case 聚合

### 已完成

- [x] 新增输入校验类 Fixture：`normalize_none_input`。
- [x] 扩展 Replay Worker，根据 `case_id` 生成对应的 read/edit/final 序列。
- [x] `run_experiment.py` 支持重复传入 `--manifest`，聚合多个 Case 的结果。
- [x] 验证 Calculator 和 Normalizer 两个 Case 均能在 Baseline/Candidate 下完成。

### 验证结果

```text
Cases: 2
Agents: 2
Trials per Case/Agent: 1
Total Trials: 4
Completed: 4/4
Evaluation passed: 4/4
```

### 下一步

增加一个故障注入版本，验证 Experiment 能识别 Candidate 回归，并开始生成机器可读报告与 CI Gate。

## 2026-08-11：代码质量审查整改 1 / P0 输出路径安全

### 已完成

- [x] Manifest `id` 和 Experiment Agent ID 限制为单个安全 slug，拒绝绝对路径、分隔符和路径穿越。
- [x] Fixture 路径必须位于 `project_root` 内，拒绝 `../` 和符号链接逃逸。
- [x] Runner 仅接受输出根目录内的安全 Job 目录。
- [x] Resume 删除前要求目录包含并匹配本次 Job 的 `run-manifest.json` Marker；未归属目录一律拒绝。
- [x] 新建 Job 先写入 Marker，已完成 Job 可安全复用。

### 验证结果

```text
unit tests: 23 total (20 passed, 3 Docker integration tests skipped by default)
path traversal / symlink escape: rejected
unowned existing output directory: rejected
marked completed job resume: passed
docker benchmark after fix: passed
```

### 下一步

整改 P1-1：让 Manifest `tool_policy.allow/deny` 真正决定 Worker 的有效工具池，并补充策略绕过回归测试。

## 2026-08-11：代码质量审查整改 2 / 执行策略与测试可信度

### 已完成

- [x] Worker 从 Trial Spec 计算有效工具池：支持能力上限 ∩ Manifest Allowlist − Denylist。
- [x] 未授权工具调用被明确拒绝并记录为 `tool.call(status=denied)`，而非静默执行。
- [x] 未知工具和永久禁止工具进入 Allowlist 时 fail-closed。
- [x] `ToolIntegrityEvaluator` 区分“被正确拒绝的尝试”和“实际越权执行”。
- [x] Benchmark/Experiment 默认使用 Docker；宿主机模式必须显式传 `--unsafe-trusted-host`。
- [x] `TestEvaluator` 要求退出码为 0 且至少执行 1 个测试，拒绝零测试假阳性。

### 验证结果

```text
unit tests: 27 total (24 passed, 3 Docker integration tests skipped by default)
read-only Manifest policy: edit_file blocked, no source change, test failed as expected
zero-test command: evaluator failed as expected
benchmark without --docker: executed successfully in Docker Sandbox
```

### 下一步

整改 P1-2：建立 Trial 总 deadline、明确 timeout 状态，并补齐 Trace、Git Evidence 和 Resume 指纹的可信链。

## 2026-08-11：代码质量审查整改 3 / Trial Deadline 与 Docker 清理

### 已完成

- [x] Parent Runner 对每个 Worker 进程强制执行 Manifest `timeout_seconds` 的 Trial 总时限。
- [x] 超时会终止整个子进程组、写入可读取的 `timed_out` 结果，并继续汇总其余 Job。
- [x] Docker Sandbox 为每次执行分配唯一容器名；Docker 客户端超时后主动执行 `docker rm --force` 清理。
- [x] 测试命令超时不再误报为 `agent_failed`，统一标记为 `timed_out`。

### 验证结果

```text
unit tests: 29 total (26 passed, 3 Docker integration tests skipped by default)
Docker integration tests: 3/3 passed
parent process deadline: passed
```

### 下一步

整改 P1-3：补齐 Trace 新鲜度/父子层级校验、Git 变更证据，以及 Resume 的 Agent 配置指纹。

## 2026-08-11：代码质量审查整改 4 / Trace、Git 与 Resume 可信链

### 已完成

- [x] 每个 Trial 创建 Trace 时先截断旧文件；校验 Trace ID、Trial ID、唯一根 Span，以及父 Span 的先后关系。
- [x] Agent、Model、Tool Span 统一挂在 `agent.run` 根 Span 下，拒绝陈旧或扁平伪造 Trace。
- [x] Git Evidence 改为相对 `HEAD` 的完整 Binary Diff；通过 intent-to-add 将未跟踪文件纳入证据，并记录基线提交与 porcelain 状态。
- [x] Resume Marker 加入 Job、Agent Version、执行模式和 Bash 设置的 SHA-256 配置指纹；配置不一致的旧结果拒绝复用。

### 验证结果

```text
unit tests: 30 total (27 passed, 3 Docker integration tests skipped by default)
real Docker Benchmark: passed
Trace: 19 events / 8 spans / all closed
Resume: same configuration reused; changed Agent Version rejected
```

### 下一步

整改 P1-4：处理 SQLite/JSONL 双写的原子性与写入失败语义，然后收敛 Manifest 中尚未实际执行的配置字段。

## 2026-08-11：代码质量审查整改 5 / Run Store 原子性

### 已完成

- [x] SQLite 在同一事务中写入 Trial、完整 Score 集合与 JSONL 审计 Outbox。
- [x] JSONL 改为 SQLite 后置、可重试的至少一次投递；每条记录提供稳定 `audit_id` 供下游去重。
- [x] JSONL I/O 失败不会回滚已提交的 SQLite 结果；下次 Store 初始化会自动补投。
- [x] 重跑 Trial 时旧 Score 集合会整体替换，避免已删除的 Evaluator 分数残留。

### 验证结果

```text
unit tests: 32 total (29 passed, 3 Docker integration tests skipped by default)
forced JSONL write failure: SQLite retained record; next Store instance recovered the audit
```

### 下一步

整改 P1-5：收敛 Manifest 中尚未真正执行的配置字段，并把结果状态与失败原因进一步结构化。

## 2026-08-11：产品化阶段 1 / 统一 Adapter Contract

### 已完成

- [x] 新增 Adapter Registry；Runner 通过 `--adapter` 选择已注册 Worker，不再硬编码 s20 Worker 路径。
- [x] 将现有确定性实现注册为 `s20-replay`，保留全部既有行为与安全边界。
- [x] Trial Spec、Result 与 Resume 指纹纳入 Adapter ID 和 Agent Version。
- [x] 新增独立的 [ADAPTER_CONTRACT.md](./ADAPTER_CONTRACT.md)，明确真实 Agent 只能负责 Agent Loop，测试、Git、Trace 校验和评分仍由平台统一负责。
- [x] Experiment Runner 可将同一 Adapter 显式传递给 Baseline/Candidate 子运行。

### 验证结果

```text
unit tests: 34 total (31 passed, 3 Docker integration tests skipped by default)
s20-replay via adapter registry: benchmark passed
```

### 下一步

产品化阶段 2：新增最小真实 ReAct Agent Adapter，并以 OpenAI-compatible Client 接入真实模型；密钥只从环境变量读取，不进入 Trial Artifact。

## 2026-08-11：产品化阶段 2 / 最小真实 ReAct Agent

### 已完成

- [x] 新增 `react-agent` Adapter：使用 OpenAI-compatible Chat Completions Function Calling 驱动 ReAct 循环。
- [x] 支持 `read_file`、`write_file`、`edit_file`、`glob`、`bash` 五种工作区受限工具；Bash 仍强制经 Docker Sandbox。
- [x] 执行 `max_tool_calls` 与 Manifest `max_tokens`；模型、工具、错误和用量会写入 Trace/Result。
- [x] `AGENT_API_KEY`、`AGENT_MODEL`、`AGENT_BASE_URL` 仅从环境变量读取，不进入任何 Trial Artifact。
- [x] 缺失凭据时明确输出 `model_failed`，不会回退为 Replay，避免伪造真实运行结果。

### 验证结果

```text
unit tests: 38 total (35 passed, 3 Docker integration tests skipped by default)
no-credential real-agent run: model_failed as expected, trace/result persisted safely
```

### 首次真实基线

```text
Case: smoke_calculator_empty_input
Agent: react-agent-v1
status / evaluation / tests: completed / passed / passed
tool calls: 7
duration: 21.0s
model tokens: 8,645
Trace: 32 events / 16 spans / valid
```

### 下一步

产品化阶段 3：扩展 Benchmark 到覆盖修复、越权、超时、失败恢复等能力的 8～12 个 Case；在接入真实模型后生成第一份真实基线数据。

## 2026-08-11：产品化阶段 3 / Core Repair Suite v1

### 已完成

- [x] Core Suite 扩展为 8 个确定性 Python 修复任务。
- [x] 新增端口默认值、Slug 标点、折扣上界、跨文件缺失字段、空配置合并、保序标签去重共 6 个 Case。
- [x] 每个 Case 都限制实现目录、禁止修改测试，并配置独立的 Token/工具调用预算。
- [x] 新增 Suite Contract Test：所有 Manifest 必须可展开，且未修复 Fixture 必须从失败测试开始。
- [x] 无依赖 YAML 解析器支持安全的单层 Flow Map/List 写法。

### 验证结果

```text
unit tests: 39 total (36 passed, 3 Docker integration tests skipped by default)
all 8 manifests: expanded successfully
all 8 fixtures: failing baseline verified
```

### 待执行

当前 Codex 进程没有继承用户终端中的 `AGENT_API_KEY` / `AGENT_MODEL`，因此未在此会话重复消耗模型配额。用户在已配置变量的终端运行新增 Case 后，即可形成 Core Suite 的真实基线。

### 首次新增 Case 基线

```text
new Cases collected: 6/6
evaluation passed: 6/6
average tool calls: 5.8
average duration: 25.6s
largest trace: safe_slug (12 model calls / 11 tool calls / 66.6s / 16,493 tokens)
```

### 下一步

产品化阶段 4：用真实 Core Suite 数据比较 `react-agent-v1` 和改进版 Candidate，输出通过率、耗时、工具调用、Token 和回归结论。

## 2026-08-11：产品化阶段 4 / v2 Candidate 准备完成

### 已完成

- [x] 根据 v1 Trace 中的重复读取、重复测试行为，新增 `verify-once-v2` Agent Profile。
- [x] v2 保持模型、工具、Sandbox 与 Evaluator 不变，只加强“最小修改 + 单次验证 + 失败后恢复”的控制策略。
- [x] 每个 Trial Result 和根 Trace Span 记录 Agent Profile，保证实验可归因。
- [x] 提供 [EXPERIMENT_V1_V2.md](./EXPERIMENT_V1_V2.md) 中的八 Case 对比命令。

### 待执行

在具有模型环境变量的终端运行完整 Baseline/Candidate Experiment，再读取 `experiment.json` 生成回归结论。

### 首次真实对比结果

```text
Cases: 8 × 1 trial × 2 versions
correctness: v1 8/8, v2 8/8
average duration: v1 15.94s → v2 12.92s (-19.0%)
average tool calls: v1 5.38 → v2 6.00 (+0.63)
average tokens: v1 6,135.5 → v2 6,321.0 (+3.0%)
conclusion: latency/tool-cost trade-off; one trial per Case is not enough to claim an overall upgrade
```

完整分析见 [EXPERIMENT_REPORT_CORE_V1.md](./EXPERIMENT_REPORT_CORE_V1.md)。

### 下一步

产品化阶段 5：建立只读 Web 控制台，展示 Trial 列表、评分、Trace、Diff 和 Baseline/Candidate 对比；保持 Runner 与真实 Agent 继续通过 CLI 执行。

## 2026-08-11：产品化阶段 5 / Read-only Console（进行中）

### 已完成

- [x] 新增只读 Dashboard Repository：从现有 Trial Artifact 构建汇总、列表、详情与 Trace 视图数据。
- [x] 新增零依赖本地 HTTP Server 和四个 JSON API；不接触模型密钥或执行链。
- [x] 增加路径穿越防护和 Dashboard Repository 测试。
- [x] 补齐 [WEB_CONSOLE.md](./WEB_CONSOLE.md) 的启动、API 与安全边界说明。

### 进行中

- [ ] 完成响应式审计台前端，并以真实 `core-experiment-v1` 数据验证页面。

### 已完成

- [x] 新增零构建依赖的响应式前端：Dashboard 指标、Trial 表、Experiment Delta、Trace 时间线与 Git Diff 检查器。
- [x] 前端在服务不可用时使用只读 Mock Fallback；正常运行时只消费本地 JSON API。
- [x] 使用真实 `core-experiment-v1` 验证 Dashboard（16 Trials）、Trial Detail（40 Trace Events + Diff）和 Experiment API（8 vs 8）。

### 下一步

产品化阶段 6：补齐一键启动、Docker 集成 CI、演示材料与简历项目描述；并将多 Trial 实验作为下一轮数据质量增强。
