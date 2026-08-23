# Changelog

本项目仍处于本地 Agent 评测平台阶段。版本号描述当前工程成熟度，不代表多租户生产服务承诺。

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
