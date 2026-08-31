# 架构讲解：从一次 Agent 运行到发布结论

这篇文档用于解释 Regression Lab 的核心工程取舍。项目不是 Agent Runtime，也不替 Agent 规划或调用工具；它负责冻结实验条件、运行两个版本、保存证据并给出可审计的发布结论。

```text
AgentSpec + Case Manifest
          ↓
   Frozen Protocol + Execution Plan
          ↓
 Trial → Attempt → Trace / Result / Git Diff / Test Evidence
          ↓
   Evaluators → Experiment Comparison → Gate
          ↓
     Console + Artifact Verify
```

## 1. 为什么先冻结 Protocol

版本对比只有在实验条件一致时才有意义。Protocol 固定 Agent 源码身份、Case、模型配置、重复次数、调度种子、执行边界和允许差异，并计算内容指纹。

Runner 启动前生成 Execution Plan，后续 Trial 必须引用同一 Protocol 指纹。这样可以区分“候选版本真的更好”和“两个版本使用了不同任务、模型或策略”。无法满足比较条件时，平台降级为不可严格比较或直接让 Gate fail closed。

## 2. 为什么 Trial 和 Attempt 分开

Trial 表示稳定的逻辑单元：`Case × Agent Version × trial_index`。Attempt 表示一次物理执行。超时、崩溃或无效 Trace 后重试时，平台创建新的 Attempt，不覆盖旧目录。

每个终态 Attempt 保存自身 Result 摘要；`selected-attempt.json` 明确指出当前采用哪次执行，Trial 根目录的 `result.json` 只是兼容投影。这解决了两个问题：重试不会混入旧 Trace，审计时也能解释最终报告来自哪次执行。

## 3. 为什么诊断指标不直接控制 Gate

Behavior Diff、Failure Attribution、平均延迟等指标适合回答“哪里变了”，但不一定适合回答“能否发布”。例如重复读取减少是好信号，却不能抵消正确率下降；模型或基础设施失败也不能从 Raw Reliability 中删除后再宣称版本可靠。

因此 Gate 只消费声明过的硬规则和阈值：正确性、完成率、Trace 完整性、策略违规和稳定性优先，Token 与工具调用成本随后判断。诊断层保持可扩展，发布层保持可解释、可复现和 fail closed。

## 4. Black-box 和 SDK Trace 的证据边界

Black-box Agent 只需接受 `--workspace` 与 `--task`。平台能独立记录进程生命周期、测试结果和 Git Diff，但看不到 Agent 内部的模型或工具调用，所以这些指标必须显示为 `N/A`，不能写成 `0`。

SDK 模式由 Agent 输出依赖无关的 JSONL Trace。`parent_span_id` 将 `agent.run`、`model.call`、`tool.call` 等 Span 组织成树，平台据此计算 Token、工具结果和行为差异。Capability 同时区分不支持、支持但未观测、已经观测，避免把“没有证据”误当成“没有发生”。

## 5. Artifact Verify 能证明什么

完整 Runtime 验证会重新检查 Protocol 指纹、Execution Plan、选中 Attempt 的内容摘要、Trial 投影、Trace 身份、Agent 源码身份，以及 Gate 是否引用当前 Experiment Comparison。它能证明：当前目录中的报告与冻结证据链保持一致，历史 Trial 没有在不更新摘要的情况下被悄悄修改。

它不能证明 Artifact 来自某个远程可信主体。`demo-manifest.json` 和 Attempt SHA-256 是完整性机制，不是数字签名；有能力同时重写文件和清单的人仍能生成一套新的自洽数据。若未来需要跨组织发布，应增加签名、可信时间戳和远程不可变存储，而不是把本地摘要描述成来源认证。

## 面试时如何演示

1. 从 Gate 说明候选版本是否允许发布，以及哪条硬规则阻断。
2. 进入同一 Case 的配对 Comparison，避免只比较全局平均数。
3. 打开 Failure Attribution，区分 Agent、模型、基础设施、证据和策略失败。
4. 展开 Trace Tree，将指标变化追溯到具体模型或工具 Span。
5. 最后运行 `make verify-runtime RUNTIME=<path>`，说明结论如何绑定到历史证据。

这条主线体现的不是页面数量，而是实验可比性、证据不可变性、失败语义和发布决策之间的边界。
