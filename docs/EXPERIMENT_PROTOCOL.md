# 实验协议与严格可比性

阶段 8 为每次新 Experiment 在执行前冻结 `protocol.json`。它不是新的事实源：Trial 的 Result、Trace、Diff 和测试 Evidence 仍是源证据；协议只记录会影响比较结论的非敏感摘要与内容指纹。

## 冻结内容

- Benchmark：Manifest、Fixture 树、测试命令、工具策略与预算的哈希；
- Agent：Adapter、版本标签、Agent 来源哈希，以及外部 Agent 握手返回的最终渲染 Prompt 集合哈希；
- Model：Provider、模型名、显式 `temperature` / `top_p` 与可选 `seed`；不保存密钥；
- 平台：Evaluator/Trace Schema 来源哈希、Python/OS、Sandbox 配置与镜像标签；
- 执行：每 Case 的重复次数与固定随机种子生成的成对交错计划。

`protocol_fingerprint` 是上述规范化对象的 SHA-256。它会写入 Experiment、每个 Attempt Manifest、选中 Attempt 索引和 Job Result。敏感环境变量（API key、Token、Authorization、Secret、Password）不会进入协议。

对于 `external-command`，严格可比性还要求每个**选中 Attempt**的运行时入口源码 Hash 与 `protocol.json` 中对应 Agent 的 `agent_source_hash` 完全一致。该 Hash 由平台 Worker 在启动 Agent 前计算，不接受 Agent 自报；Hash 缺失或不一致时，报告标记为 `not_comparable`，Gate 不能将其解释为可晋级证据。`--report-only` 也会从选中 Attempt 的 Result 重新核验这一点。

参考外部 Agent 支持 `--describe-protocol` 握手：平台通过标准输入发送版本标签和各 Case 的测试命令，Agent 只返回 Profile ID 与最终 System Prompt 集合的 SHA-256，不返回 Prompt 正文。缺少握手的新外部 Experiment 会在执行前失败关闭，避免把相同源码 Hash 误当成不同 Prompt 已被冻结。

未配置采样环境变量时，平台和两套 OpenAI-compatible 客户端共同采用 `temperature=0.0`、`top_p=1.0`。`AGENT_SEED` 只有在显式设置时才发送给 Provider；未设置时协议记录 `not_configured`，不再用含义不清的 `null`。若 Provider 不支持 seed，后续应通过 Provider 能力声明记录 `unsupported`，不能由“未配置”推断。

## Resume 与可比性

- 同一输出目录的 `--resume` 只允许相同协议；模型、Case、预算、源码、Sandbox 或执行种子变更会默认拒绝；
- 即使某个 Trial 已经存在，外部 Agent 源码在 Attempt 间发生变化也不能被后续报告重建掩盖：新 Attempt 必须使用新协议，旧新混合证据只能是 `not_comparable`；
- `--allow-protocol-mismatch` 仅用于保留诊断性 Artifact，新版本协议会另存，Experiment 标记为 `not_comparable`；
- 有协议的 Experiment 只有 `strict` 时可由 Gate 给出 `promote`；非严格协议结论为 `inconclusive`；
- 协议冻结之前的历史 Artifact 维持可读，但标记 `legacy_unverified`，不能声称严格可比。

## 交错执行

`execution-plan.json` 对每个相同 `Case × Trial` 固定 Baseline/Candidate 顺序，并在各配对之间使用固定 Seed 打乱。这样避免总是先跑完一个版本而把模型服务负载或时间段变化误认为版本性能差异。计划的 `schedule_index` 随 Attempt Evidence 一同保存。

## 当前边界

- Docker 镜像当前记录标签 `python:3.11-slim`，尚未解析到不可变 Digest；因此跨时间的 Docker 环境仍存在残余漂移风险。
- 外部 Agent 的工具 Span 和 Token Usage 仍适用于可信本地 Agent，不是针对恶意进程的防篡改证明。
- Prompt 正文不落盘，只记录最终渲染 Prompt 集合哈希；外部第三方 Agent 必须实现协议描述握手才能获得严格 Prompt 可比性。
