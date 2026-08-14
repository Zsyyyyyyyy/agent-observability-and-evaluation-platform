# Coding Agent Regression Lab 执行计划

> 执行周期：14 天（简历可投递版 v0.1）  
> 工作目录：`study/Regression/`  
> 交付基线：`docs/DELIVERY_CHECKLIST.md`  
> 首个被测对象：只读接入 `legacy agent` Coding Agent

## 1. 执行目标

在 14 天内完成一个可运行、可复现、可演示的 Coding Agent 回归评测闭环：

```text
版本化 Case
  → 隔离运行 Agent
  → 采集 Trace 与 Git Diff
  → 执行确定性 Evaluator
  → 比较 Baseline/Candidate
  → 生成报告
  → CI Gate 判断是否回归
```

v0.1 的成功标准不是功能数量，而是以下四件事全部成立：

1. 同一个任务可以在固定环境下重复运行。
2. 两个 Agent 版本可以执行配对对照实验。
3. 每个结论都能追溯到 Trace、Diff、日志和测试证据。
4. 已知回归能够被 CI Gate 稳定发现。

## 2. 固定约束

- 所有新增与修改仅发生在 `study/Regression/`。
- 不修改 `legacy agent` 源码，通过 Adapter 只读接入。
- Agent 编排在宿主机运行，危险工具在 Docker 沙箱中运行。
- Trace 使用 OpenTelemetry 并发送到 Phoenix。
- 元数据使用 SQLite，Artifact 使用本地 SHA-256 内容寻址存储。
- MVP 不做多租户、LLM Judge、通用 Agent SDK 和完整自研前端。
- 未通过当前阶段验收门，不提前开始后续阶段。

## 3. 关键路径

```text
Schema 冻结
  ↓
legacy agent 单 Case 可运行
  ↓
Trace + Worktree + Docker 闭环
  ↓
Artifact + Evaluator
  ↓
多 Case / 多 Trial 调度
  ↓
Baseline vs Candidate 对比
  ↓
报告 + CI Gate
  ↓
真实实验 + 故障注入
  ↓
README / Demo / 简历材料
```

Phoenix UI、报告美化和更多 Benchmark 都不能阻塞前四个核心步骤。

## 4. 阶段总览

| 阶段 | 时间 | 核心目标 | 阶段输出 | 验收门 |
|---|---:|---|---|---|
| P0 设计冻结 | Day 1 | 固定数据契约、CLI 和边界 | Schema、ADR、目录骨架 | Gate 0 |
| P1 风险验证 | Day 2–3 | 打通一个 Case 的最小闭环 | Trace、Diff、测试结果 | Gate 1 |
| P2 运行基础设施 | Day 4–5 | 持久化、幂等和证据链 | SQLite、Artifact、状态机 | 内部验收 |
| P3 隔离执行 | Day 6–7 | 工具沙箱和 Worktree 生命周期 | Docker Runner、安全测试 | 沙箱验收 |
| P4 评测体系 | Day 8–9 | Benchmark 与六类 Evaluator | 8–10 Cases、Score Evidence | Gate 2 |
| P5 实验与门禁 | Day 10–11 | 多 Trial、对比和 CI | JSON/HTML/JUnit、退出码 | Gate 3 前置 |
| P6 实证验证 | Day 12–13 | 真实实验和故障注入 | 48–60 Trials、回归案例 | Gate 3 |
| P7 发布交付 | Day 14 | 文档、演示和简历材料 | README、Demo、简历描述 | Gate 4 |

## 5. Phase 0：设计冻结（Day 1）

### 5.1 任务

- [ ] 建立自包含项目目录和独立 `pyproject.toml`。
- [ ] 定义 `AgentVersion` Schema。
- [ ] 定义 `CaseVersion` Schema。
- [ ] 定义 `Experiment`、`Trial` 和 `RunManifest` Schema。
- [ ] 定义 `TraceRecord`、`SpanRecord` 和 `EventRecord` 最小字段。
- [ ] 定义 `Artifact`、`Score` 和 `Evidence` Schema。
- [ ] 定义 Trial 状态机和状态转换规则。
- [ ] 定义基础设施失败、Agent 失败、超时和不完整 Trace 的语义。
- [ ] 定义 CLI 的目标接口。
- [ ] 记录关键架构决策 ADR。

### 5.2 预期 CLI 契约

```bash
# 校验配置
arlab config validate

# 运行单次 Trial
arlab trial run --agent AGENT.yaml --case CASE.yaml

# 运行完整实验
arlab experiment run --benchmark BENCHMARK.yaml --agents baseline.yaml,candidate.yaml --trials 3

# 评测已有 Trial
arlab evaluate TRIAL_ID

# 生成报告
arlab report build EXPERIMENT_ID

# 执行回归门禁
arlab gate check EXPERIMENT_ID --policy gate.yaml
```

这些命令在 Day 1 只冻结接口，不要求全部实现。

### 5.3 Gate 0 验收

- 所有 Schema 有字段说明和示例。
- 不可变对象和可变执行状态边界清楚。
- 所有失败状态有唯一语义。
- CLI 输入输出和退出码有定义。
- 目录外修改为零。

## 6. Phase 1：最小风险闭环（Day 2–3）

### 6.1 Day 2：legacy agent Adapter 与基础 Trace

- [ ] 分析 legacy agent 的模型调用、工具注册、循环和压缩边界。
- [ ] 设计只读 Adapter，不直接修改 legacy agent 文件。
- [ ] 运行一个固定 Prompt。
- [ ] 创建根 Span `agent.run`。
- [ ] 包装模型调用为 `model.call` Span。
- [ ] 包装工具调用为 `tool.call` Span。
- [ ] 捕获异常并设置 Span Status。
- [ ] 将 Trace 发送到本地 Phoenix。

当天验收：

- Phoenix 能看到一条父子关系正确的 Trace。
- Trace 包含模型耗时和至少一次工具调用。
- legacy agent 文件内容和 Git 状态未被修改。

### 6.2 Day 3：单 Case、Worktree 与测试证据

- [ ] 创建第一个最小 Fixture 仓库。
- [ ] 创建一个版本化 Case YAML。
- [ ] 从固定 Base Commit 创建临时 Worktree。
- [ ] 让 Agent 在 Worktree 中完成任务。
- [ ] 执行测试命令并保存退出码。
- [ ] 收集 Git Diff、stdout 和 stderr。
- [ ] 生成最小 Trial Result。
- [ ] Trial 完成后可靠清理临时资源。

### 6.3 Gate 1 验收

必须同时满足：

- 单 Case 能端到端运行。
- Phoenix 中存在完整 Trace。
- 能得到 Git Diff。
- 能判断测试通过或失败。
- 失败时仍能保存日志和 Trial 状态。

若 Gate 1 未通过：

- 暂停 UI、SQLite 完整建模和多 Case 开发。
- 优先解决 Adapter、执行边界或 Trace 完整性。
- 最多允许将 Phoenix 暂时降级为本地 OTLP/NDJSON 调试，但不能取消 Trace 语义。

## 7. Phase 2：运行基础设施（Day 4–5）

### 7.1 Day 4：SQLite 与状态机

- [ ] 实现数据库初始化和版本管理。
- [ ] 实现 AgentVersion、CaseVersion、Experiment、Trial 元数据存储。
- [ ] 实现 Trial 状态转换校验。
- [ ] 实现创建 Trial 的幂等键。
- [ ] 实现中断后的 Experiment 恢复查询。
- [ ] 为不可变对象增加修改拒绝测试。

### 7.2 Day 5：Artifact Store 与脱敏

- [ ] 实现 SHA-256 内容寻址写入。
- [ ] 实现 Artifact 去重和完整性校验。
- [ ] 保存 Git Diff、测试日志、stdout、stderr 和 RunManifest。
- [ ] 将大字段从 Trace 改为 Artifact Reference。
- [ ] 对 API Key、Authorization、Cookie 和环境变量进行脱敏。
- [ ] 建立 Trial → Score → Evidence Artifact 的追溯链。

### 7.3 阶段验收

- 重复写入同一 Artifact 不产生重复文件。
- 已完成 Trial 不被恢复流程覆盖。
- Artifact 损坏能够被 Hash 校验发现。
- 任何测试报告都能反向定位到 Trial 和 Trace。

## 8. Phase 3：隔离执行（Day 6–7）

### 8.1 Day 6：Docker Tool Runner

- [ ] 设计 Host Agent → Tool Proxy → Docker Runner 调用链。
- [ ] Shell 和测试命令只在容器内执行。
- [ ] 使用固定 Image Digest。
- [ ] 容器根文件系统只读。
- [ ] 只挂载当前 Trial Worktree。
- [ ] 默认关闭网络。
- [ ] 配置 CPU、内存、PID 和运行时间限制。
- [ ] 只透传环境变量白名单。

### 8.2 Day 7：安全边界与资源清理

- [ ] 阻止绝对路径访问。
- [ ] 阻止 `../` 越界。
- [ ] 验证 Agent 无法访问其他 Case Worktree。
- [ ] 验证容器无法访问公网。
- [ ] 验证无限循环被超时终止。
- [ ] 验证子进程数量受限。
- [ ] 验证取消后容器及子进程全部退出。
- [ ] 验证清理逻辑不会删除原始仓库。

### 8.3 沙箱验收

- 五类安全测试全部通过。
- Agent 编排进程不直接执行模型生成的宿主机 Shell。
- 超时和取消均产生结构化 Trial 状态和 Evidence。
- 文档明确说明沙箱的能力边界，不宣称绝对安全。

## 9. Phase 4：Benchmark 与 Evaluator（Day 8–9）

### 9.1 Day 8：Benchmark

优先完成 8 个 Case，时间允许扩展到 10 个：

| Case 类型 | 目标能力 | 主要风险 |
|---|---|---|
| 边界条件修复 | 定位简单 Bug | 空修改或误修 |
| 输入校验 | 添加约束 | 过度拒绝 |
| 小型功能 | 多文件理解 | 漏改调用方 |
| 受约束重构 | 保持行为 | Diff 过大 |
| 异步错误 | 理解执行顺序 | 只修表象 |
| 工具失败恢复 | 观察重试策略 | 重复死循环 |
| 禁止修改测试 | 路径策略 | 修改 Oracle |
| 长上下文任务 | 压缩与记忆 | 丢失目标 |
| 配置修复（可选） | 依赖分析 | 修改无关文件 |
| 多步骤修复（可选） | 规划与验证 | 提前终止 |

每个 Case 都必须：

- 固定 Base Commit。
- 可以独立执行测试。
- 不依赖未固定远程数据。
- 有 Allowed/Forbidden Paths。
- 有明确预算和超时。
- 至少有一个失败前状态和一个正确解。

### 9.2 Day 9：六类 Evaluator

- [ ] TestEvaluator。
- [ ] PathPolicyEvaluator。
- [ ] DiffEvaluator。
- [ ] ToolIntegrityEvaluator。
- [ ] BudgetEvaluator。
- [ ] TraceCompletenessEvaluator。
- [ ] 每个 Evaluator 保存实际值、阈值、结论和 Evidence。
- [ ] 每种 Evaluator 至少覆盖一个通过和一个失败测试。

### 9.3 Gate 2 验收

- 至少 8 个 Case 通过 Manifest 校验。
- 六类 Evaluator 均可独立运行。
- 单 Trial 可以完整生成全部 Score。
- Score 不依赖不可解释的 LLM Judge。
- 所有失败结论都有具体 Evidence。

## 10. Phase 5：实验、报告与 CI Gate（Day 10–11）

### 10.1 Day 10：多 Trial Experiment Runner

- [ ] 定义 Baseline AgentVersion。
- [ ] 定义 Candidate AgentVersion。
- [ ] 支持 Case × AgentVersion × Trial 的任务展开。
- [ ] 默认每个 Case 执行 3 次。
- [ ] 支持失败继续和断点恢复。
- [ ] Agent 失败不重试；基础设施失败最多重试一次。
- [ ] Experiment Summary 区分有效 Trial 和无效 Trial。

### 10.2 Day 11：报告与门禁

- [ ] 生成 `report.json`。
- [ ] 生成静态 `report.html`。
- [ ] 生成 `junit.xml`。
- [ ] 生成 Phoenix Trace 深链接。
- [ ] 对比成功率、测试结果、Token、P95 延迟、工具调用和 Diff。
- [ ] 标记 Improved、Unchanged、Regressed 和 Invalid。
- [ ] 实现 Gate Policy 配置。
- [ ] 实现正确的 `0/非 0` 退出码。
- [ ] 编写 Mock/Replay CI Workflow 示例。

### 10.3 Gate Policy 初始规则

```text
阻断：Forbidden Path 修改 > 0
阻断：Trace 不完整
阻断：必须通过的 Case 测试失败
阻断：总体成功率下降超过配置阈值
告警/可配置阻断：P95 延迟上涨超过 20%
告警/可配置阻断：平均 Token 上涨超过 15%
```

## 11. Phase 6：真实实验与故障注入（Day 12–13）

### 11.1 Day 12：预实验

- [ ] 使用 2 个 Case × 2 个版本 × 3 次 Trial 做预实验。
- [ ] 检查模型费用、超时、随机性和 Trace 完整性。
- [ ] 调整预算和 Gate 阈值，但不能根据结果随意修改正确答案。
- [ ] 固定最终 BenchmarkVersion 和 AgentVersion。
- [ ] 记录实际模型和执行环境。

### 11.2 Day 13：完整实验

最低目标：

```text
8 Cases × 2 AgentVersions × 3 Trials = 48 Trials
```

理想目标：

```text
10 Cases × 2 AgentVersions × 3 Trials = 60 Trials
```

同时注入两个明确回归：

1. 最大循环轮数过低，导致复杂任务提前终止。
2. 移除路径保护，导致修改测试或无关文件。

完整记录：

- 哪些 Case 发生回归。
- 哪个 Trace Span 首先暴露异常。
- 哪个工具调用或策略造成失败。
- Git Diff 和测试 Evidence 如何证明问题。
- 修复后重新实验是否恢复。

### 11.3 Gate 3 验收

- 完成至少 48 个有效 Trial。
- 两个注入回归均被 Gate 发现。
- 至少有一个完整“发现—定位—修复—验证”案例。
- 报告中的所有数值均可追溯到 Trial。
- 无效 Trial 不混入成功率和性能统计。

## 12. Phase 7：发布与求职交付（Day 14）

### 12.1 README

- [ ] 项目背景和痛点。
- [ ] 与 Phoenix/Langfuse/Opik 的职责边界。
- [ ] 5 分钟 Mock Quick Start。
- [ ] 真实实验运行说明。
- [ ] 架构图和数据流。
- [ ] Benchmark 和 Gate 示例。
- [ ] 真实实验结果表。
- [ ] 一次回归定位案例。
- [ ] 安全边界、随机性和已知限制。
- [ ] 上游项目及组件归属说明。

### 12.2 演示材料

- [ ] 一份脱敏 HTML 样例报告。
- [ ] Phoenix Trace 截图。
- [ ] CI Gate 失败截图。
- [ ] 3 分钟演示视频或 GIF。
- [ ] 一条单 Trial 演示命令。
- [ ] 一条完整 Experiment 演示命令。

### 12.3 简历材料

- [ ] 2～3 条基于真实指标的项目描述。
- [ ] 个人负责部分和上游复用部分分开表述。
- [ ] 准备 5～8 个面试追问的答案。
- [ ] 准备架构取舍、失败案例和下一步规划。

### 12.4 Gate 4 验收

- 新环境能按 README 运行 Mock Demo。
- 报告、Trace、Diff 和测试 Evidence 可以互相跳转或定位。
- 演示流程在 3 分钟内完整跑通。
- 简历中不包含无法复现或夸大的指标。

## 13. 测试执行策略

每个阶段都遵循相同顺序：

```text
Schema/单元测试
  → 组件测试
  → 单 Case 集成测试
  → 多 Trial 集成测试
  → Mock/Replay CI
  → 真实模型实验
```

测试分层：

| 层级 | 是否调用模型 | 是否启动 Docker | 用途 |
|---|---:|---:|---|
| Unit | 否 | 否 | Schema、状态机、Evaluator、Artifact |
| Component | Mock | 可选 | Adapter、Trace、Runner |
| Integration | Replay/Mock | 是 | 单 Trial 完整闭环 |
| Experiment | 是 | 是 | Baseline/Candidate 真实对比 |

真实模型实验不作为每次代码提交的必跑测试，避免费用和随机性导致 CI 不稳定。

## 14. 进度管理规则

每天结束时更新：

- 当天完成的 Task ID。
- 新增或变更的测试数量。
- 当前 Gate 状态。
- 已知风险与阻塞。
- 第二天唯一最高优先级任务。

完成定义：

- “写完代码”不等于完成。
- 必须有测试、文档和可观察输出。
- 只有通过阶段验收条件，任务才能勾选完成。
- 未产生真实 Evidence 的指标不能写入 README 或简历。

## 15. 风险与降级方案

| 风险 | 触发信号 | 降级方案 |
|---|---|---|
| legacy agent 难以只读注入 Trace | Day 2 仍无法拦截模型/工具边界 | 在 Adapter 中建立最小兼容执行层，保留 legacy agent 行为和来源说明 |
| Phoenix 启动或 OTLP 不稳定 | Day 3 无法稳定展示 Trace | 暂存标准 OTLP/NDJSON，先保证 Trace 模型，随后恢复 Phoenix |
| Docker Tool Proxy 复杂度过高 | Day 6 无法执行基础工具 | 先支持 Shell/Read/Write/Edit 四类必要工具，删除非关键工具 |
| Benchmark 制作耗时 | Day 8 少于 8 个有效 Case | 保证 8 个高质量 Case，不追求 10 个 |
| 真实模型成本过高 | 预实验超过预算 | 降低 Case 数到 8，保持 3 Trials；CI 使用 Replay |
| 模型随机性导致 Gate 抖动 | 同配置结果波动明显 | 使用三次 Trial、配对比较和区间/阈值，不用单次结果阻断 |
| 自研报告页面拖期 | Day 11 报告 UI 未完成 | 输出可靠 JSON + JUnit + 简单静态 HTML，不做交互前端 |
| 时间不足 | 任一阶段晚于计划 2 天 | 取消 P1 内容、UI 美化和第 9/10 个 Case，保住端到端闭环 |

## 16. 最终发布顺序

```text
冻结 v0.1 Schema 与 Benchmark
  → 运行 Mock/Replay 全套测试
  → 运行真实预实验
  → 运行 48～60 Trial 完整实验
  → 生成并脱敏报告
  → 验证 CI Gate
  → 完成 README 和 Demo
  → 更新简历
```

在 v0.1 完成前，任何新增需求都必须回答一个问题：

> 它是否直接提高“可复现、可比较、可追溯、可门禁”四项核心能力？

如果不能，则进入 v0.2 Backlog，不进入当前执行周期。
