# Regression Lab 独立技术评审

> 评审角色：独立技术评审者 + AI Agent 面试官
> 评审范围：当前 `study/Regression` 源码、实验 Artifact、测试、文档与前端
> 评审性质：只读侧面审查，不代表发布审计，也不改变项目状态

## 结论摘要

当前项目已经不是“learn-claude-code 加了一个页面”，而是一个具有独立技术主线的本地 Coding Agent 回归评测平台雏形。

最准确的定位是：

> 面向 Coding Agent 的本地可复现回归实验与发布决策系统：将 Agent 作为被测对象，在隔离工作目录中重复执行修复任务，采集 Trace、测试、Diff 和成本证据，对 Agent 版本进行比较、诊断与 Gate 决策。

当前招聘判断：

| 维度 | 评分 |
| --- | ---: |
| 秋招价值 | 7.2 / 10 |
| 实习价值 | 8.0 / 10 |
| 继续投入潜力 | 9.0 / 10 |
| Evaluation 成熟度 | 6.5 / 10 |
| Execution Reliability | 6.5 / 10 |

主要归类：**Evaluation 平台雏形**。

如果能够真正讲清楚 Trial/Attempt、实验可比性、失败归因、统计边界和执行隔离，它可以表现为“有真实系统设计深度的 Agent 工程项目”；如果只会演示页面和背 README，仍可能被归类为“AI Coding 堆出来的大项目”。

## 一、项目现在到底是什么

### 1. 解决的问题

平台解决的不是“如何做一个更聪明的 Agent”，而是：

- Agent 修改代码后，结果是否正确；
- 是否修改了不允许修改的文件；
- Trace 和工具调用证据是否完整；
- 新 Prompt、新工具策略或新版本是否真的优于旧版本；
- 一次成功是否只是偶然；
- 版本变化是否带来了延迟、Token 或工具成本回归；
- 失败后能否定位到模型、基础设施、证据、策略或 Agent 本身。

### 2. 目标用户

当前真实用户主要是：

- Coding Agent 开发者；
- Prompt、Tool、Agent Loop 调优人员；
- 需要决定某个 Agent 版本能否替换旧版本的研发人员。

当前是单机开发工具，不是多租户 SaaS。

### 3. 核心使用流程

```text
Benchmark Manifest
    ↓
展开 Case × Version × Trial
    ↓
为物理 Attempt 创建独立工作目录
    ↓
通过 Adapter 运行 Agent
    ↓
采集 JSONL Trace + Git Diff + 测试结果
    ↓
6 类确定性 Evaluator
    ↓
Experiment 聚合、版本对比、统计区间
    ↓
Promotion Gate
    ↓
Evolution Catalog + Dashboard
```

核心入口：

- `scripts/run_benchmark.py`：单 Case/Trial 执行、Attempt、Resume、超时和 Artifact；
- `scripts/run_experiment.py`：多版本、多 Case、多 Trial 对照实验；
- `src/regression_lab/evaluators.py`：确定性评测器；
- `src/regression_lab/experiment.py`：聚合、配对比较和统计；
- `src/regression_lab/gate.py`：候选版本晋级规则；
- `src/regression_lab/evolution_catalog.py`：版本与实验历史索引；
- `src/regression_lab/dashboard.py`：只读查询模型。

### 4. Agent 在系统中的角色

Agent 是被测系统（System Under Test）。平台不依赖 Agent 自报“我成功了”，而是独立执行测试、收集 Git Diff、校验 Trace 并运行 Evaluator。

当前主要接入方式：

- `react-agent`：平台自带的最小真实模型 Coding Agent；
- `external-command`：通过环境变量和 JSONL Observer 接入外部 Agent；
- `s20-replay`：课程项目的确定性回放桥，主要用于兼容和链路验证。

### 5. 当前可视化内容

- Promotion Gate 总览；
- Baseline/Candidate 通过率与成本差异；
- Case 级两版本成对柱状图；
- Trial 的延迟、Token、工具调用；
- Agent、模型、工具 Span 时间线；
- 真实工具名称；
- Git Diff；
- 统计置信区间；
- Agent 版本演进时间线。

因此它已经不只是日志展示，但仍主要依赖人工从证据中定位根因。

### 6. 当前评测内容

单 Trial 目前包含六类确定性评测：

1. 测试是否真实运行并通过；
2. 修改路径是否合法；
3. Diff 是否为空、过大或包含异常变更；
4. Trace 是否完整且身份匹配；
5. 工具 Span 是否闭合、是否存在未授权工具；
6. 工具调用次数和 Agent 根 Span 耗时是否超过预算。

实验层面进一步聚合：

- completion/test/evaluation pass rate；
- 模型、Trace、基础设施失败率；
- P50/P95 latency；
- Token 和工具调用数；
- Case 重复实验稳定性；
- 重复读、重复工具调用、先改后读等行为指标；
- 按 Case 聚类的配对 Bootstrap 置信区间。

### 7. 已形成的核心模块

- Agent Adapter；
- Benchmark Manifest；
- Trial/Attempt 执行；
- Trace Schema 与验证；
- Git/Test Evidence；
- 确定性 Evaluator；
- Experiment Comparison；
- Promotion Gate；
- Evolution Catalog；
- 本地只读 Dashboard。

### 8. 半成品与未闭环能力

仍处于半成品状态的主要能力：

- 外部 Agent 的安全隔离；
- 严格实验可比性；
- Evolution Catalog 的真实版本治理；
- 行为诊断；
- Retry 策略；
- Artifact、SQLite 和 Catalog 的一致性；
- Benchmark 的广度与难度。

Manifest 中以下字段存在，但真实执行路径没有完整消费：

- `max_retries`；
- `setup_command`；
- `environment_allowlist`；
- `artifacts.required`；
- `evaluators.required`；
- `acceptance.must`。

这属于“Schema 看起来完整、运行时没有兑现”的明显 AI 味来源。

### 一句话简历定位

> 面向 Coding Agent 的可观测回归评测与版本发布决策平台，支持外部 Agent 接入、隔离执行、Trace/Diff/Evidence 采集、重复实验、统计比较和 Promotion Gate。

## 二、与 learn-claude-code 的关系

### 原课程提供的思想来源

真正影响当前项目的主要是：

- Agent loop；
- 工具调用模式；
- 文件读写、编辑和 Bash 工具；
- Tool Policy / Permission Hook；
- 错误恢复和上下文处理的概念；
- Git 工作目录隔离的思想。

### 不是当前平台核心能力的课程模块

以下能力不是 Regression Lab 的主运行链路：

- Skills；
- Sub-agent；
- Agent Teams；
- Task System；
- 对话 Memory；
- MCP；
- Cron；
- Autonomous Agent。

不要把这些全部算作当前平台能力。

### 当前项目真正新增的能力

完全新增或大规模重构的部分包括：

- Benchmark Manifest 与 Case 模型；
- Trial/Attempt 模型；
- Trace 合同与完整性校验；
- Result/Score Schema；
- SQLite + JSONL Outbox；
- 多版本重复实验；
- 确定性 Evaluator；
- Failure Attribution；
- Promotion Gate；
- Case 聚类 Bootstrap；
- Evolution Catalog；
- Dashboard 和 Case Comparison；
- 框架无关的外部 Agent Observer SDK。

### 关系判断

如果面试官熟悉 learn-claude-code，合理的判断应该是：

> Regression Lab 受课程启发、保留可选兼容桥，但核心数据模型、评测链路和版本治理已经独立，不是“课程代码加页面”。

### 一个需要修正的表述

当前执行代码使用 `shutil.copytree`、`git init` 和临时 Git 仓库，并不是真正的 `git worktree add`。因此更准确的表述是：

> 每个 Attempt 使用独立的 Git 工作目录/临时仓库。

除非后续实现真正的 Git Worktree，否则面试时不应把它说成严格意义上的 Git Worktree 隔离。

## 三、秋招/实习角度锐评

### 主要归类

**Evaluation 平台雏形。**

它不是 CRUD 或纯 Dashboard Demo，因为源码中存在：

- 独立执行边界；
- Evidence；
- 重复实验；
- 失败状态；
- 版本对比；
- Gate；
- 统计分析；
- 真实模型实验。

但它还不是成熟的通用 Evaluation Platform，主要原因是：

- Benchmark 只有 8 个非常小的 Python 修复任务；
- 大部分 Case 是单函数级别；
- 实验协议没有完整锁定；
- 外部 Agent 的 Trace 和 Token 仍由 Agent SDK 自报；
- 没有大规模运行调度；
- 没有真正通用的语言/框架支持；
- 没有线上流量或真实任务数据；
- 没有模型定价成本；
- 没有开放任务的 Judge 体系。

## 四、技术主线判断

当前主线是成立的：

```text
Agent 不确定性
    ↓
Execution Trace
    ↓
Repeated Experiment
    ↓
Deterministic Evaluation
    ↓
Version Comparison
    ↓
Promotion Decision
    ↓
Agent Optimization
```

### 已成立的环节

- Trace；
- Case/Trial 重复运行；
- 确定性测试和策略评测；
- 两版本对照；
- 失败归因；
- Gate；
- 版本历史；
- 统计区间；
- 结果下钻到 Trace/Diff。

### 仍偏概念的环节

- Agent Optimization 目前依赖人看报告后修改 Prompt；
- 系统没有自动生成可验证的优化假设；
- Version Snapshot 不够完整；
- 历史实验的严格可比性还不可靠；
- Dashboard 能展示差异，但失败根因分析仍主要靠人工。

### 最严重的缺失

最严重的不是缺少更多 Agent 功能，而是：

> 实验协议没有被完整冻结，导致“Agent A 是否真的比 Agent B 好”的结论仍可能受到模型配置、代码内容、执行时间和基础设施变化的污染。

这条主线值得继续强化，而且比继续增加 MCP、Skills、RAG 或对话 Memory 更适合秋招。

## 五、模块评级

| 模块 | 评级 | 判断 |
| --- | :---: | --- |
| Agent Execution | A | 有真实模型和外接 Agent，但平台重点不应放在自带 Agent |
| Trace | S | 项目证据链核心，必须完全吃透 |
| Observability | S | Trace、Result、Diff、Score 串联是真正亮点 |
| Experiment | S | 项目技术主线核心 |
| Case | S | 决定评测质量，当前数量和难度不足 |
| Trial | S | 正确表达随机 Agent 的重复实验单位 |
| Attempt | A | 对 timeout/resume/污染隔离有真实必要性 |
| Evaluation | S | 最值得写进简历，但要诚实描述边界 |
| Comparison | S | Case 配对、P50/P95、Bootstrap 有讨论价值 |
| Agent Version | A | 有必要，但 Snapshot 仍不完整 |
| Resume | A | 保留失败证据、不静默覆盖的设计合理 |
| Retry | C | Manifest 中存在，真正自动 Retry 尚未落地 |
| Timeout | A | 进程组清理和 Docker 清理较扎实 |
| Process Isolation | A- | 工作目录隔离有效，但 Agent 进程本身未容器化 |
| Result Persistence | A- | SQLite 事务和 Outbox 有价值，但事实源并未统一 |
| Failure Attribution | B | 有用的确定性诊断，不是智能根因分析 |
| Behavior Metrics | B | 重复读、工具错误等有价值，但维度还浅 |
| Evolution Catalog | B+ | 方向正确，当前实现略重、部分信息为推断 |
| Dashboard | A | 已与 Experiment、Gate、Trace、Diff 联动 |
| S20 Replay | C | 兼容与回归用途，别继续投入太多 |
| 自带 React Agent | B | 参考 Agent 足够，不应扩展成另一个大项目 |

## 六、AI 味与过度设计

当前 AI 味整体为中等，约 **6/10**。问题不是代码风格，而是部分协议成熟度明显超过真实产品成熟度。

### 1. 多套事实视图

同一份 Trial 信息同时存在于：

- Attempt Result；
- Job-level Result；
- SQLite；
- JSONL Audit；
- Summary；
- Experiment；
- Evolution Catalog。

Dashboard 直接递归读取 `result.json`，没有使用文档中称为“事实源”的 SQLite。Worker 写 SQLite 失败时也只是附加 `store_error`，不会让 Trial 失败。

建议明确选择：

- Artifact 是唯一证据源，SQLite/Catalog 是可重建索引；或
- 所有查询统一走 SQLite。

当前两种设计混在一起。

### 2. Evolution Catalog 有过度推断

Catalog 自动推断：

- Agent 身份；
- Baseline/Candidate 状态；
- Change Type；
- 父版本关系；
- Attempt 时间。

其中部分不是运行时真实记录，而是事后从目录和标签推断。建议保留 Catalog，但缩减为 Artifact 索引，不要自动发明过多版本治理状态。

### 3. Manifest 字段过多但不生效

没有执行语义的字段应暂时删除、降级为实验字段，或真正实现。尤其 `max_retries` 会误导使用者认为已有自动重试。

### 4. 入口脚本职责过重

`run_benchmark.py` 同时负责 CLI、Manifest、Output ownership、Resume、Attempt、工作目录、Git、Worker、超时、Result 和 Summary。建议后续收敛为一个明确的 `TrialRunner`，但不需要引入庞大的 Manager/Service/Repository 层级。

### 5. Adapter 代码存在重复

`react-agent`、`external-command`、`s20` 重复了 Git Evidence、测试执行、Trace 校验、Evaluation、Store 和 Result 写入。建议抽取一个小型平台 Finalizer，减少重复，但避免大规模抽象。

### 6. 报告字段重复

`experiment.json` 同时保存 `comparison.case_comparisons` 和顶层 `case_comparisons`，统计字段也有类似重复。建议保留一个规范 Schema，由 API 层负责前端格式转换。

## 七、Evaluation 含金量审查

### 已有含金量

- Case 由确定性 Fixture 和测试定义；
- 同一 Case 重复运行三次；
- Baseline/Candidate 使用相同 Case；
- 同 Trial Index 形成配对视图；
- 失败不会静默覆盖；
- Trace 不合法不能算有效通过；
- 同时计算 raw reliability 和排除外部故障后的 agent quality；
- Bootstrap 按 Case 聚类，避免把同一 Case 的三次重复当成三种独立任务；
- 统计诊断和工程 Gate 分离。

### 主要不足

#### Benchmark 外部有效性不足

现有核心 Case 多为：

- 空输入；
- `None`；
- 字符串处理；
- 小型列表去重；
- 简单跨文件修改。

它们适合验证系统链路，不足以代表真实 Coding Agent 能力。后续应增加多文件依赖、隐藏测试、配置兼容、错误处理、状态迁移、小型重构和性能约束任务。

#### 当前 `Pass@3` 命名不够准确

实现要求一个 Case 的三次 Trial 全部通过，本质更接近：

> `all-pass@3` 或 Case consistency rate

不应直接称为标准 Pass@3。

#### 版本执行顺序可能污染 latency

当前实验通常先跑完 Baseline，再跑 Candidate，不是随机交错运行。模型服务负载、网络和时间段变化可能影响性能比较。

#### 可比性指纹不完整

当前 Fingerprint 没有完整固定：

- Agent 源码 Hash；
- Prompt Profile 内容 Hash；
- 模型供应商和精确模型版本；
- Temperature/Seed；
- 工具 Schema Hash；
- Evaluator 代码 Hash；
- Docker Image Digest；
- Python/OS 环境；
- `agent_profile` 本身。

#### External Agent 的 Trace 不是防篡改证据

外部 Agent 可以访问 Trace Path、Trace ID、Trial ID 和 Agent Version，因此可以伪造工具 Span 和 Token Usage。当前接入方式只适用于受信任本地 Agent。

#### Gate 存在边界问题

当 Baseline 平均工具调用或 Token 为0时，Ratio 直接返回0，Candidate 新增成本可能仍通过。另有部分配置名称表达绝对失败率，但实现比较的是 Candidate-Baseline delta，名称与语义需要统一。

### 当前缺失指标

- 模型调用成本；
- 测试覆盖变化；
- Patch 正确性之外的代码质量；
- 工具选择准确率；
- 测试重试次数；
- 上下文压缩/长度；
- 首次正确修改率；
- 恢复能力；
- 开放任务下的 Judge Score。

LLM-as-Judge 不是当前第一优先级。对于确定性 Coding Case，测试和策略评测更可信。

## 八、Experiment / Trial / Attempt 设计

| 层级 | 实际含义 |
| --- | --- |
| Agent | 一个稳定的被测 Agent 家族 |
| Agent Version | Prompt、代码、模型或工具配置的某个版本 |
| Benchmark | 一组 Case |
| Experiment | 同一协议下对 Baseline/Candidate 的一次比较 |
| Case | Fixture、任务、测试和策略 |
| Trial | Case × Agent Version × 重复序号 |
| Attempt | 一次真实进程执行 |

Trial 与 Attempt 分开是合理的：Trial 是统计单位，Attempt 是物理执行。超时、崩溃和重跑都不能复用原 Trace，旧失败证据也必须保留。

当前仍需改进：

- `max_retries` 没有形成自动 Retry；
- Resume 主要是重新运行未完成任务；
- Corrupt Result 恢复不够健壮；
- Experiment CLI 没有完整传递 `--rerun-invalid`；
- 父进程超时生成的 Result 不一定进入 RunStore；
- Catalog 中 Attempt 时间不是实际执行时间；
- Trial 身份在 Job、Artifact 和 Catalog 之间存在多套 ID。

## 九、执行可靠性

### 做得好的地方

- 每个 Attempt 独立目录；
- 新 Attempt 不复用旧 Trace；
- Trace ID、Trial ID、Agent Version 有身份校验；
- Worker 和外部 Agent 使用独立进程组；
- timeout 后先 SIGTERM，再 SIGKILL；
- Docker timeout 后强制删除容器；
- Job Fingerprint 防止错误复用输出目录；
- Resume 默认不覆盖已完成但无效的证据；
- SQLite Trial+Score 事务写入；
- JSONL 使用 Outbox 至少一次投递；
- Agent Output 使用临时文件替换。

### 主要短板

#### External Agent 运行在宿主机

Docker 主要用于 Bash 工具和最终测试；外部 Agent 进程本身在宿主机运行。准确边界是：

> 只接入使用者明确配置且信任的本地 Agent。

不能包装成可安全托管任意不可信 Agent 的平台。

#### Environment Allowlist 未真正执行

外部 Agent Environment 基于完整 `os.environ` 构造，Manifest 中的 `environment_allowlist` 没有形成真实隔离。

#### Result 写入并非全部原子化

Agent Output 和 Catalog 使用原子替换，但 Job-level Result、Summary、Experiment 主要是直接写入，中断时可能留下截断 JSON。

#### 缺少并发控制

目前主要通过顺序执行避免冲突，但没有 Output Directory Lock、Attempt ID 原子分配、SQLite 并发策略和同一 Experiment 重复启动防护。不要并发运行两个指向同一 Output Dir 的实验。

#### Docker 镜像未固定 Digest

使用 `python:3.11-slim` 标签，未来内容可能变化，影响严格复现。

#### 缺少的关键测试

- 进程被强杀后的完整 Resume 集成测试；
- Corrupt Result 恢复；
- 同 Output Dir 并发启动；
- SQLite、Artifact、Catalog 一致性；
- Docker Image 漂移；
- 外部 Agent 伪造 Trace；
- Baseline/Candidate 随机交错实验。

## 十、可视化技术价值

当前大致处于 **Level 4+，正在进入 Level 5**：

- 不是单纯展示日志；
- 能展示 Agent execution trace；
- 能查看工具和状态证据；
- 能定位到失败 Trial 和 Diff；
- 能与 Evaluation、Experiment、Gate、Version Comparison 联动。

还没有完全达到成熟 Level 5，因为无法自动指出哪个行为差异导致了失败，Trace 也不展示完整上下文与决策状态。

秋招做到稳定 Level 5 已足够。最值得增加的是：

> 点击某个回归指标，直接列出贡献最大的 Case → 成对 Trial → Trace 行为差异。

比继续增加动画、图表和首页卡片更有价值。

## 十一、面试前必须吃透的 20% 源码

### 第一优先级：必须完全理解

1. `scripts/run_benchmark.py`：Fingerprint、Resume、Attempt、Worker、超时和 Result 选择；
2. `src/regression_lab/attempts.py`：Trial 与 Attempt 的分离原因；
3. `src/regression_lab/runner.py`：进程组清理；
4. `adapters/external_command/worker.py`：平台和外部 Agent 的信任边界；
5. `src/regression_lab/schema.py`：Trace 顺序、身份和 Span 生命周期验证；
6. `src/regression_lab/evaluators.py`：六类 Evaluator 分别防什么问题；
7. `src/regression_lab/experiment.py`：valid pass、重复实验、配对、Bootstrap 和统计边界；
8. `src/regression_lab/gate.py`：统计诊断和发布 Gate 为什么分开。

### 第二优先级：理解设计和数据流

- `src/regression_lab/manifest.py`；
- `src/regression_lab/store.py`；
- `src/regression_lab/evolution_catalog.py`；
- `src/regression_lab/behavior.py`；
- `src/regression_lab/attribution.py`；
- `src/regression_lab/dashboard.py`；
- `adapters/react_agent/worker.py`；
- `src/regression_lab/sdk.py`。

### 第三优先级：知道作用即可

- S20 Replay；
- Failure Probe；
- OpenAI-compatible 请求解析细节；
- Web CSS；
- YAML 简易解析器；
- 发布文档和演示脚本。

## 十二、建议的后续优先级

按招聘价值排序：

1. **冻结实验协议**：保存 Agent 源码 Hash、Prompt Hash、模型配置、工具 Schema、Evaluator Hash、镜像 Digest、预算与运行时间。
2. **修正评测语义**：重命名当前 `Pass@3`，修复 Gate 的零基线 Ratio 和绝对值/Delta 命名问题。
3. **扩充 Benchmark**：从8个玩具任务扩展到20～30个分层任务，加入多文件、隐藏测试、错误恢复和小型重构。
4. **强化真实可比性**：Baseline/Candidate 随机交错运行，固定采样参数，区分全尝试指标、成功条件指标和外部故障条件指标。
5. **统一事实源**：明确 Artifact 是 Source of Truth，SQLite/Catalog 都是可重建索引，减少重复 JSON。
6. **做一次真实回归案例**：专门构造或找到一个真实 v4 退化，让系统从 Gate → Case → Trial → Trace 精确定位原因。
7. **收敛安全表述**：要么真正容器化外部 Agent，要么明确只支持可信本地 Agent。

## 最终判断

项目已经拥有一条真实且有区分度的技术主线，最有价值的部分是：

> **证据化的 Agent 版本回归决策。**

不是 Agent 功能数量。

如果下一阶段把实验协议、Benchmark 质量和事实源一致性做扎实，项目具备从当前约7分提升到8.5分以上的潜力。
