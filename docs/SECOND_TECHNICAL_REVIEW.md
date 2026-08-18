# Regression Lab 第二轮独立技术复审

> 复审角色：Senior AI Agent Engineer、Agent Evaluation / Observability 系统架构师、秋招技术面试官
>
> 复审性质：只读审查当前源码、测试、运行产物和文档；本轮不修改代码，不运行真实模型实验。

## Executive Verdict

### 一句话锐评

项目已经从 AI 辅助堆出的可观测 Demo，进化成了有真实实验闭环的 Coding Agent 回归系统；现在最大的敌人不是功能不足，而是 Gate 和证据一致性还没有配得上它展示出来的专业感。

### 当前结论

**项目已经发生明显质变，最终评级为 A：已经很有竞争力，还差最后的可信决策闭环。**

它已经不是“课程 Agent 加了一个页面”，而是一个面向 Coding Agent 的本地可复现回归实验与版本发布决策系统。不过，它还不是成熟的通用 Evaluation Platform，也不应包装成商业化 SaaS、自动优化平台或生产级分布式调度系统。

### 最强的地方

V3 → V4 → V4.1 的真实假设、失败、修正和 48-Trial 验证链：

- 8 Case × 3 Trial × 2 Version；
- Baseline/Candidate 各 24 个有效 Trial；
- 正确率和 Trace 有效率均为 100%；
- Candidate 平均延迟降低约 7.77 秒；
- 平均模型 Token 减少约 6972；
- 平均工具调用减少约 1.67；
- 24/24 Candidate Trace 执行了确定性成功停止；
- 成功停止后没有新增模型或工具 Span。

### 最弱的地方

Promotion Gate 不是完全 fail-closed，且 Artifact、SQLite、JSONL、Evolution Catalog 之间存在重复事实源，选中 Attempt 的语义尚未完全收口。

### 最大技术风险

系统可能对“同样失败”的 Baseline 和 Candidate 给出 PROMOTE；不同存储对选中 Attempt 的认识也可能不一致。

### 最大秋招风险

代码和概念很多，如果无法解释 Trial/Attempt、Protocol、Gate、Bootstrap 和失败边界，面试官会把项目判断为 AI 堆砌。

### 当前最像真项目的部分

- Attempt 独立隔离与 Resume；
- 外部 Agent 接入契约；
- Trace、测试、Diff 的平台侧证据；
- V4.1 成功停止不变量审计；
- 真实多 Case、多 Trial 对照实验。

### 当前最像 AI 堆出来的部分

- Evolution/Storage 多套模型和尚未完全落地的治理叙事；
- 文档中已描述、但代码尚未消费的 Pricing、Cost、LLM Judge 等能力；
- 一些没有真实 Consumer 的未来扩展 Schema。

### 最值得吃透的三个设计

1. Trial / Attempt 的生命周期和故障恢复；
2. Experiment Protocol / Comparability 的公平比较边界；
3. Deterministic Evaluation / Promotion Gate 的证据到决策链。

### 最应该停止做的三件事

1. 继续扩展 Dashboard 页面；
2. 继续增加新的 Schema 和抽象层；
3. 继续接入更多 Agent Framework、MCP、RAG、Memory 或 SaaS 能力。

### 下一阶段唯一第一优先级

修复 Gate 和执行证据链的 fail-closed 可靠性。

### 进入“简历强项目”状态的条件

完成以下三个条件即可：

1. Gate 反例全部被阻断；
2. Artifact、SQLite、Catalog 的选中 Attempt 完全一致且可重建；
3. 正对照能 PROMOTE、负对照能 HOLD 的正式实验通过。

### 面试官判断

如果候选人能够真正掌握上述核心设计，我愿意围绕这个项目深挖 30 分钟。

---

## 1. 当前项目实际上发展成了什么

### 当前真正解决的问题

它解决的不是“如何做一个更聪明的 Agent”，而是：

> 当 Coding Agent 的 Prompt、执行策略或 Runtime 发生变化时，如何通过可重复实验判断新版本是否更正确、更稳定、更高效，并下钻到 Trace、工具调用、测试和 Git Diff 解释差异。

### 核心用户

- Coding Agent 开发者；
- Prompt、Agent Loop、Tool Policy 调优人员；
- 需要决定 Candidate 能否替换 Champion 的研发人员。

当前系统是单机、本地、开发期工具，不是多租户 SaaS。

### 真实工作流

```text
Agent Version / Prompt Profile
        ↓
冻结 Experiment Protocol 与执行顺序
        ↓
Case Manifest × Version × Trial
        ↓
Attempt 独立目录和临时 Git 仓库
        ↓
Adapter 启动内置或外部 Agent
        ↓
Trace + Git Diff + Test Evidence
        ↓
确定性 Evaluators
        ↓
Case/Trial 聚合与配对统计
        ↓
Promotion Gate
        ↓
Evolution Catalog
        ↓
Dashboard 下钻至 Trial / Trace / Diff
```

核心入口：

- `scripts/run_benchmark.py`：单 Case/Trial 执行、Attempt、Resume、超时和 Artifact；
- `scripts/run_experiment.py`：多版本、多 Case、多 Trial 对照实验；
- `src/regression_lab/evaluators.py`：确定性评测器；
- `src/regression_lab/experiment.py`：聚合、配对比较和统计；
- `src/regression_lab/gate.py`：候选版本晋级规则；
- `src/regression_lab/evolution_catalog.py`：版本与实验历史索引；
- `src/regression_lab/dashboard.py`：只读查询模型。

### 一句话定位

> 以当前真实代码状态，这个项目本质上是一个面向 Coding Agent 的本地可复现回归实验与版本发布决策系统。

---

## 2. 上一阶段改造的价值判断

### A. 真正的技术升级

#### Trial / Attempt 分离

原问题：Retry、Timeout、Resume 会覆盖历史执行证据。

新设计：每个逻辑 Trial 下保存多个物理 Attempt，每个 Attempt 有独立 Worktree、Trace、Result 和状态。

判断：真实解决了重试污染和历史证据覆盖问题，是核心技术升级。

依据：

- `src/regression_lab/attempts.py` 的 `AttemptManager`；
- `tests/test_attempts.py`；
- `tests/test_runner_safety.py` 的 Resume 测试。

#### Experiment Protocol 冻结与交错执行

原问题：模型、Prompt、Fixture、工具策略或运行顺序变化会污染版本比较。

新设计：

- Fixture、Manifest、Prompt、Agent 源码和模型配置哈希；
- 固定执行计划；
- Baseline/Candidate 成对交错执行；
- 协议不一致时拒绝 Resume；
- Gate 只接受严格可比实验。

判断：明显提升实验可信度。

注意：V3 → V4.1 的协议意图不能简单称为纯 Prompt Ablation，因为 V4.1 还增加了确定性 Runtime Stop。更准确的说法是：

> Prompt Profile + Runtime Success-Stop Policy 的混合干预。

#### 真实 V3 → V4.1 实验

这部分已经有真实 Artifact 和专项审计支撑，不是 Schema 展示。它是当前项目产生质变的主要证据。

#### 外部 Agent 接入与平台证据所有权

外部 Agent 只能写有限的 `agent_response` 和退出原因；Trial 身份、测试、Diff、Score 和协议身份由平台生成。

判断：这是有实际价值的 Agent Engineering 设计。

### B. 合理工程完善

- Failure Attribution；
- Behavior 指标；
- V1 → V4.1 谱系声明；
- Dashboard 诊断链；
- V4.1 专项审计命令；
- SQLite + JSONL Outbox；
- Docker 隔离增强。

这些有价值，但不应与真实实验闭环争夺项目主线。

### C. 表面升级

- Evolution Schema 和校验函数比实际治理能力更完整；
- `evaluators.required`、`acceptance.must` 被 Manifest 校验，但执行链没有真正根据这些字段组装 Evaluator；
- Metrics 文档描述了价格表、Cost per valid pass、LLM Judge，但当前代码未落地；
- Evolution Catalog 中部分治理状态依赖人工配置，不是实验结论自动生成。

### D. AI 过度设计

当前最明显的三个复杂设计：

1. SQLite + JSONL Outbox + Artifact + Catalog 四层事实副本；
2. Evolution 中大量治理状态，但 Champion 晋级仍是人工配置；
3. `openai_compatible.py` 与 `react_agent/model_client.py` 存在重复模型客户端实现。

---

## 3. 技术主线成立度

**8.2 / 10**

| 环节 | 状态 |
|---|---|
| Agent Version | 基本成立 |
| Case | 已成立 |
| Trial | 已成立 |
| Attempt | 基本成立 |
| Trace | 已成立 |
| Deterministic Evaluation | 已成立 |
| 重复实验 | 已成立 |
| Version Comparison | 已成立 |
| Statistical Evidence | 基本成立 |
| Gate | 部分成立，存在严重边界缺陷 |
| Candidate / Champion Governance | 部分成立 |
| Console Diagnosis | 基本成立 |
| 自动 Optimization | 不存在，当前靠人工 |

现在更准确的叙事是：

> 平台不是自动优化 Agent，而是让开发者通过可信证据完成“提出假设—对照实验—定位差异—人工决策—产生新版本”的循环。

---

## 4. Evaluation 深度审查

### 已真正实现

- 单 Case 多 Trial；
- Baseline/Candidate 同 Case、同 Trial Index 配对；
- 测试正确性；
- Path/Diff Policy；
- Trace 完整性；
- Tool Integrity；
- Budget；
- Completion、Evaluation、Test Pass Rate；
- 模型、基础设施、Trace、策略失败率；
- P50/P95 Latency 和 Token；
- all-pass@3 consistency；
- flaky case rate；
- 8 Case 聚类 Bootstrap；
- 行为差异与失败归因；
- 严格协议比较；
- 确定性 Gate。

### 尚未实现或可信度有限

- LLM-as-Judge；
- 开放式任务质量评测；
- 模型价格与 Cost per valid pass；
- 隐藏测试；
- 大型真实仓库任务；
- 跨模型、跨 Provider 复现实验；
- Seed 能力声明；
- Benchmark 污染和 Agent 过拟合防护；
- Gate 的绝对质量下限。

### 已验证的 Gate 反例

当前 Gate 中的失败率规则比较的是：

```text
candidate_rate - baseline_rate <= threshold
```

而配置语义看起来更像是：

```text
candidate_rate <= absolute_threshold
```

因此当 Baseline 和 Candidate 都是 100% 基础设施失败时，二者差值为 0，Gate 可能给出 PROMOTE。

这说明当前 Gate 是“非退化 Gate”，还不是“达到最低发布质量的 fail-closed Gate”。

同时，缺失指标在部分路径可能默认成 0，也会削弱可信度。

评分：

- Evaluation Design：8.2 / 10；
- Evaluation Implementation：7.4 / 10；
- Evaluation Credibility：7.1 / 10。

V4.1 这次具体实验仍然可信，因为专项审计额外验证了 48 个 Trial 全部成功；但通用 Gate 不能视为已经完全可靠。

---

## 5. Trial / Attempt 模型锐评

### Experiment

一次冻结了被比较版本、Benchmark Cases、Trial 数、模型、Prompt、执行计划、平台和 Sandbox 的完整对照运行。

定义合理。

### Case

逻辑 Benchmark Task，由 Manifest + Fixture + Test + Policy 组成，不是执行实例。

定义正确。

### Trial

一个 `Case × Agent Version × repetition index` 的逻辑实验样本，用于测量 Agent 随机性和 Provider 波动。

定义正确。

### Attempt

Trial 的一次物理执行。Retry 不应覆盖历史证据，因此分离有真实必要性。

但当前仍有三个不足：

1. Attempt 终态与 Result 状态存在两套语义，容易混淆；
2. “最佳 Attempt”优先选择历史有效 Pass，会隐藏此前发生过的失败；
3. SQLite 的 `selected` 与 Artifact 的 `selected-attempt.json` 可能分叉。

结论：Attempt 不是过度建模，而是正确抽象；问题在于选中语义和统计语义还没有收口。

---

## 6. Execution Reliability

| 能力 | 判断 |
|---|---|
| Attempt 独立目录 | 已实现 |
| 独立 Trace / Result / Worktree | 已实现 |
| Trial 防重复并发 | 未实现，没有锁 |
| Agent 超时回收进程组 | 已实现 |
| Worker 进程组硬截止 | 已实现 |
| Docker 超时强制删除容器 | 已实现 |
| Host Test 子进程泄漏控制 | 仍有风险 |
| Crash 不生成伪成功 | 基本成立 |
| Invalid Attempt 不进入当前统计 | 基本成立 |
| Resume 未完成任务 | 已实现 |
| Retry 保留历史 Attempt | 已实现 |
| Corrupted Artifact 识别 | 部分成立 |
| 并发执行隔离 | 不同 Trial 成立；同一 Trial 不成立 |
| Experiment 状态可重建 | 部分成立 |
| 严格状态机 | 未完全实现 |
| Artifact/SQLite/Catalog 一致性 | 尚未保证 |

关键问题：

### 缺少 Trial 级锁

`AttemptManager.create_attempt()` 通过扫描目录计算下一个编号。两个进程并发 Resume 同一 Trial 时，可能竞争同一个 Attempt 编号。

### Result 不是统一原子写

多个核心 Result、Summary 和 Attempt Manifest 使用直接 `write_text`。进程在写入中途崩溃可能留下截断 JSON。

### 实际事实源不清晰

虽然 `RunStore` 文档称 SQLite 为权威，但 Dashboard 读取 Artifact，Experiment 读取 Summary，Catalog 再索引 Artifact，Attempt 又有独立的 selected 文件。

建议明确：

```text
Immutable Attempt Artifact = 源证据
selected-attempt.json = 当前 Trial 投影
SQLite / JSONL / Catalog = 可重建索引
```

评分：**Execution Reliability：6.8 / 10**

如果现在执行串行的 `3 Case × 3 Trial × 2 Version`，结论是：**基本相信**。串行本地实验的主要链路已经可靠，但并发、Crash Consistency 和多源一致性还不足以“完全相信”。

---

## 7. Schema 与真实实验的关系

答案是：**已经不是“Schema 很强、实验很弱”。**

48 次真实 V3/V4.1 实验让项目跨过了关键门槛。

但实验边界仍然明确：

- 真实确认实验覆盖 8 Cases；
- Fixture 总代码量较小；
- 多数任务是几十行 Python；
- 测试对 Agent 可见；
- 没有隐藏测试；
- 没有中型真实仓库；
- 两个版本最终都 24/24 通过，主要区分的是效率而不是正确率；
- Bootstrap 只能支持“在这 8 个 Case 上观察到一致效率改善”，不能支持广泛泛化结论。

所以当前阶段应该是：

> 停止新增横向抽象，先修可信度边界，再做带负对照的真实实验。

---

## 8. Console 价值判断

### 当前能力等级

| Level | 状态 |
|---|---|
| 1. 发生了什么 | 已成立 |
| 2. Agent 为什么这样执行 | 基本成立 |
| 3. 哪个 Case 失败 | 已成立 |
| 4. 哪个 Trial 不稳定 | 基本成立 |
| 5. 为什么 Candidate 更好/差 | 基本成立 |
| 6. 下一步优化哪里 | 部分成立 |

**当前 Console Level：5 / 6。**

Console 已经能展示 Gate、Protocol、Case 配对、Trial 指标、Trace Timeline、真实工具名称、Git Diff、Reliability、Bootstrap、Failure Attribution、V4.1 Stop Evidence 和版本演进。

但它还不能稳定回答：

- 下一步应修改 Prompt、Runtime 还是 Tool Policy；
- 某行为差异与最终质量的因果关系；
- 当前结论是否受到 Benchmark 偏置或 Case 泄漏影响。

继续做 UI 的 ROI 已经较低。除非修复数据显示错误，否则不应继续增加页面、动画和图表。

---

## 9. 复杂度减法建议

### 保留

- Case / Trial / Attempt；
- Protocol；
- Trace；
- Deterministic Evaluator；
- Experiment Comparison；
- Gate；
- External Adapter；
- Dashboard 下钻。

### 简化

- 拆分 `run_benchmark.py` 的 Runner、Attempt Coordinator、Artifact Publisher 职责；
- 统一两个 OpenAI-compatible Client；
- 简化 Evolution Validation 与 Runtime Model 的重复状态；
- Behavior 与 Failure Attribution 保持诊断层，不继续扩展成规则引擎。

### 合并

- Artifact Selector 与 SQLite Attempt Selection；
- Result/Score 写入与 Atomic Artifact Publisher；
- Version Profile 与 Evolution Snapshot。

### 暂时删除或冻结

- 未使用的 LLM Judge Schema；
- 未落地的 Pricing/Cost 字段；
- 没有 Consumer 的 Manifest 字段；
- 继续增加 Evolution 实体类型。

---

## 10. 学生项目复杂度与面试可解释性

评分：

- 代码规模合理性：7.1 / 10；
- 架构复杂度合理性：6.8 / 10；
- 学生可掌握程度：6.5 / 10；
- 面试可解释性：8.5 / 10。

项目复杂度已经接近个人项目上限。

> 应该停止横向扩功能，开始纵向吃透。

---

## 11. Pareto 学习清单

### S：必须完全吃透

1. `scripts/run_benchmark.py`：Trial/Attempt、Resume、超时、Evidence、结果发布；
2. `src/regression_lab/attempts.py`：Trial 与 Attempt 的分离和选中策略；
3. `adapters/external_command/worker.py`：外部 Agent 与平台证据边界；
4. `src/regression_lab/evaluators.py`：从 Result/Trace/Diff 生成可信 Score；
5. `src/regression_lab/experiment.py`：Case 配对、稳定性、Bootstrap；
6. `src/regression_lab/protocol.py`：公平比较和协议冻结；
7. `src/regression_lab/gate.py`：Promotion 规则及当前缺陷；
8. `examples/external_openai_agent.py`：V1–V4.1 的真实区别。

### A：理解设计即可

- `trace.py` / `schema.py`；
- `runner.py` / `sandbox.py`；
- `behavior.py` / `attribution.py`；
- `store.py`；
- `run_experiment.py`；
- `evolution_catalog.py`；
- Manifest expansion。

### B：知道职责即可

- Dashboard HTTP glue；
- 前端 DOM 渲染；
- JSON serialization；
- S20 Replay；
- Failure Probe；
- 文档脚本和页面样式。

---

## 12. 项目所有权测试：面试追问

### 现在应该能够 defend

1. 为什么把 Agent 当作被测系统，而不是平台内部组件？
2. 为什么不能相信 Agent 自报成功？
3. Case、Trial、Attempt 分别解决什么问题？
4. 为什么 Retry 不能覆盖旧 Trial Result？
5. 为什么同一 Case 要重复运行？
6. 为什么按 Case 聚类 Bootstrap，而不是把 24 个 Trial 当成独立任务？
7. 为什么执行计划要 Baseline/Candidate 交错？
8. Trace 完整性如何校验？
9. 外部 Agent 能写什么、不能写什么？
10. 模型失败、基础设施失败、Agent 失败如何区分？
11. 为什么 LLM Judge 不进入硬 Gate？
12. V4 为什么失败，V4.1 为什么有效？
13. 为什么成功后强制 Stop 属于 Runtime Policy，而不仅是 Prompt？
14. 为什么不直接比较平均 Token？
15. 为什么不直接采用 LangSmith、Langfuse 或 TruLens？

### 目前容易被问穿

16. 为什么两个版本都完全失败时 Gate 还能 PROMOTE？
17. Gate 为什么没有 Candidate 的绝对最低通过率？
18. SQLite 声称权威，为什么 Dashboard 不读 SQLite？
19. Job Result 和 SQLite 对同一个 Attempt 的 selected 不一致怎么办？
20. 两个进程同时 Resume 同一个 Trial 会发生什么？
21. Result 写到一半进程崩溃，如何恢复？
22. Retry 后一次成功，之前的模型失败是否应计入可靠性？
23. 为什么 Protocol 声明 prompt-only，但 V4.1 包含 Runtime 改动？
24. 测试对 Agent 可见，如何避免 Benchmark Overfitting？
25. Docker 镜像只有 Tag，没有 Digest，跨时间如何复现？
26. 外部 Agent 自己写 Trace，恶意或错误 Agent 如何防伪？
27. 为什么 `max_tokens` 在 Protocol 中显示为 `[redacted]`？
28. Champion 切换为什么不是 Gate 的事务性结果？
29. 如何证明 V4.1 的提升能泛化到真实仓库？
30. 当前 Cost 指标在哪里，价格版本是什么？

---

## 13. 与普通学生 Agent 项目的差异化

| 维度 | 评分 |
|---|---:|
| Agent Engineering 深度 | 8.2 |
| Evaluation 深度 | 7.7 |
| Reliability | 6.8 |
| Experiment Design | 8.0 |
| Observability | 8.3 |
| 系统设计 | 7.8 |
| 工程真实性 | 7.3 |
| 可量化程度 | 8.8 |
| 个人技术贡献感 | 6.8 |
| 面试讨论空间 | 9.0 |
| 秋招差异化 | 8.4 |

它已经明显跳出“LangGraph + RAG + MCP + Chat UI”层次，因为核心问题变成了：

> 如何证明一次 Agent 改动真的更好。

---

## 14. 当前可以写进简历的内容

### 已完成且可以直接写

- 外部 Coding Agent 的框架无关接入协议；
- Trial/Attempt 隔离执行、超时、Resume 和历史证据保留；
- Trace、测试和 Git Diff 的平台侧确定性评测；
- 多 Case、多 Trial、双版本对照实验；
- Case 配对与聚类 Bootstrap；
- V3 → V4.1 的真实优化实验；
- 24/24 Candidate Trace 成功停止不变量验证；
- 可下钻到 Trial/Trace/Diff 的只读 Console。

### 已实现但暂时不要重点写

- Evolution Catalog；
- SQLite JSONL Outbox；
- S20 Replay；
- 静态前端细节；
- Manifest 校验器数量。

### 做完实验后才能写

- Production-grade Promotion Gate；
- 并发安全实验调度；
- Cost per valid pass；
- 大型真实仓库 Benchmark；
- Regression Dataset；
- LLM Judge。

### 当前绝对不能写

- 通用商业化 Eval SaaS；
- 自动优化 Agent；
- 自动 Champion 发布；
- 统计显著性泛化结论；
- 安全执行任意第三方 Agent；
- 多租户；
- 大规模分布式调度；
- 完整 LLM Judge 系统。

### 最值得放在简历上的三个技术贡献候选

1. Trial/Attempt 隔离、失败恢复和可审计证据链；
2. 严格实验协议、成对重复实验和聚类 Bootstrap；
3. V4.1 确定性成功停止策略及 48-Trial 真实对照验证。

---

## 15. 下一阶段优先级

### 第一优先级：F —— 修执行可靠性和 Gate 可信边界

这是当前秋招 ROI 最高的方向。项目已经有真实实验，不缺页面；面试官最容易深挖的是“为什么相信 PROMOTE”。

后续排序：

1. Gate 和执行证据链可靠性；
2. 带正负对照的 Candidate vs Champion 实验；
3. 稳定 Regression Evaluation；
4. 扩展 Benchmark；
5. 成本等补充 Metrics；
6. Console 小幅完善；
7. 架构扩展和新功能。

### P0-1：修 Gate Fail-Closed 语义

#### Problem

两个版本都完全失败时可能得到 PROMOTE；缺失指标也可能被当成 0。

#### 为什么现在必须解决

Gate 是整个项目的最终结论层。它错，前面的 Trace、统计和 Dashboard 越完善，错误结论越有迷惑性。

#### 完成标准

- Candidate 必须达到绝对最低完成率和评测通过率；
- Infra/Trace/Policy 检查 Candidate 绝对值；
- 缺失指标返回 `not_available` 并阻断；
- Case/Trial 覆盖不足返回 `INCONCLUSIVE`；
- 全失败、空数据、部分缺失输入全部 fail-closed。

#### 如何验证

加入以下反例测试：

- 0% vs 0%；
- 100% infra failure vs 100%；
- 无 Token；
- 少一个 Case；
- 少一个 Trial；
- 非 strict protocol。

#### 对简历价值的变化

可以真实讲“设计并实现可靠 Promotion Gate”，而不只是“做了指标比较”。

### P0-2：统一 Artifact 真相源与 Attempt Selection

#### Problem

Artifact、SQLite、JSONL、Catalog 的选中 Attempt 可能不同。

#### 完成标准

明确以下架构：

```text
Immutable Attempt Artifact = 源证据
selected-attempt.json = 当前 Trial 投影
SQLite / JSONL / Catalog = 可重建索引
```

Selection 只能在一个组件发生，其他存储必须从它重建。

#### 如何验证

- Attempt-1 pass、Attempt-2 model failure；
- Artifact、SQLite、Dashboard、Catalog 选择结果一致；
- 删除 SQLite 后可从 Artifact 重建；
- 重建不改变实验结论。

#### 对简历价值的变化

从“多份存储”升级为“事件证据 + 可重建投影”的清晰架构。

### P0-3：补并发、Crash 和原子写故障测试

#### Problem

当前主要测试 Happy Path 和顺序 Resume。

#### 完成标准

- 同一 Trial 同时运行只有一个执行者；
- Attempt 分配不冲突；
- 所有关键 JSON 原子替换；
- Crash 后 Running Attempt 可识别；
- Resume 不覆盖旧证据；
- Host Test 子进程不会泄漏，或明确禁止 Host 正式实验。

#### 如何验证

用故障注入测试：

- 写 Result 前 Kill；
- 写 Result 中 Kill；
- 两个进程同时 Resume；
- Agent 创建子进程后超时；
- Docker 客户端被中断。

#### 对简历价值的变化

Execution Reliability 有机会从约 6.8 提升到 8 分以上。

### P1：P0 完成后再做

1. 带负对照的正式实验；
2. 增加 2–3 个中型、多文件、隐藏测试 Case；
3. 冻结并精简项目文档与面试材料。

### STOP：当前明确不做

- 新 UI 页面；
- LLM Judge；
- RAG/MCP/Memory；
- SaaS、账号和权限；
- 新增 Evolution 实体；
- 接入更多 Agent Framework；
- 继续新增指标名，除非已经有真实 Consumer。

---

## 16. 下一轮正式实验建议

在 P0 完成前，**暂时不应立即运行下一轮正式模型实验**。

阻塞项：

1. Gate 的绝对阈值语义错误；
2. 缺失数据没有完全 fail-closed；
3. Attempt Selection 多源不一致；
4. 没有同 Trial 并发锁；
5. V3 → V4.1 的协议意图需要明确为 Prompt + Runtime 混合干预。

P0 修复后建议做三臂实验：

```text
Champion：V3
Positive Candidate：V4.1
Negative Control：保留一次成功后的冗余模型调用版本
```

建议规模：

```text
优先：8 Cases × 3 Trials × 3 Versions = 72 Trials
成本允许时：8 Cases × 5 Trials × 3 Versions = 120 Trials
```

实验必须回答：

> 平台是否既能批准一个真正更好的版本，也能阻止一个人为构造的退化版本？

最低可靠性标准：

- Protocol strict；
- 全部 Case 覆盖完整；
- 无 Infra/Trace failure；
- Candidate 不降低正确性；
- Positive Candidate 的效率区间位于 0 以下；
- Negative Control 被 Gate HOLD；
- 缺数据返回 INCONCLUSIVE。

---

## 17. 最终建议

当前项目已经足够进入简历，但不应继续以“增加功能”作为主要进展指标。

接下来最应该做的，不是继续增加功能，而是：

> **证明系统在失败、缺失、重试、并发和负对照下，仍然不会得出错误的发布结论。**
