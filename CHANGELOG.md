# Changelog

本项目仍处于本地 Agent 评测平台阶段。版本号描述当前工程成熟度，不代表多租户生产服务承诺。

## 1.3.1 - 2026-08-30

### Fixed

- Failure Span 对齐现在按同一条结构匹配 Trace Diff 行判断，不再错误比较跨运行的随机 Span ID。
- Gate 分别验证模型与工具成本证据来源，避免未观测模型指标误拒绝可信的 LangGraph 工具 Trace。
- Trace Diff 改为带父子关系的同步双列树；完整保留 Added/Removed 子树，并以因果前序确定首个行为分叉。
- 首个行为分叉优先定位工具或模型操作变化；父节点状态变化会等待子树诊断完成后再作为兜底原因。
- 直接点击 Compare traces 会打开 Trace Inspector；首分叉定位会同步展开祖先节点。

## 1.3.0 - 2026-08-30

### Added

- 运行环境身份指纹：解释器、平台与依赖集合以非敏感哈希冻结，并在 Trial 中复核。
- Gate Evidence Policy：核心平台证据与模型/工具成本证据按 `evidence_provenance` 区分来源。
- LangGraph Trace Conformance 自检，覆盖工作流层级、流式回调、并行节点与异常闭合。
- 同步双列 Trace Diff，展示结构分叉、Span 时长/Token/工具调用差异、关键路径与失败 Span 对齐。
- Studio 取消、原 Runtime 续跑，以及重启 Studio 后发现并恢复已取消实验。

### Changed

- Runtime mismatch 现在以 `environment_mismatch` 记录，不再误标为 `trace_incomplete`。
- Gate 对历史无 provenance 的 Artifact 保持兼容；新 Trial 则以来源可信度参与结论。

## 0.2.0 - 2026-08-24

### Added

- Studio 无 YAML 双版本 Quick setup，以及仅保存在浏览器内的本地配置恢复。
- 按 `parent_span_id` 展示的 Trace Tree、Failure Attribution 和版本行为对比。
- 完整 Experiment Artifact Verify 与可校验的公开 Demo 导出器。
- Instrumented `PROMOTE` Demo 和 LangGraph 黑盒 `HOLD` Demo。
- `make verify`、Docker 边界测试和确定性 Failure Suite。

### Changed

- 外部 Agent 默认改为显式环境变量白名单，不再继承完整平台环境。
- Experiment、Benchmark、Attempt、Evaluator、Gate 和 Schema 主链路完成可读性整理。
- README 收敛为五分钟技术主线，并明确本地单用户和可信 Agent 边界。

### Security

- 公开导出会脱敏报告 JSON 与 Trace JSONL 中的本机路径和常见密钥形式。
- Artifact 与 Demo 校验检测相对于冻结摘要的内容变化；它们不等价于数字签名或来源认证。

> 仓库历史曾使用过过早的 `v1.0.0` 标签。当前重新以 `v0.2.0` 表达实际成熟度；稳定远程服务、签名 Artifact 和多租户隔离不在本版本承诺内。
