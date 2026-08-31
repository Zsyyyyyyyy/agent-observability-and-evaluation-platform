# Roadmap

当前发布线是 v1.3。本页只记录尚未实现的候选能力；它们需要先评估价值、兼容性和验证成本，不能被当作稳定承诺。v1.3 仍是可信本地 Agent 的评测平台，不代表远程多租户服务已成熟。

## 未排期候选

- 扩展真实模型实验的 Case 覆盖与重复次数，积累更强的版本比较证据。
- 在不改变 Behavior Diff v1 语义的前提下，评估新的可观测模式（例如 context compaction、subagent retry、handoff）。
- 评估更多外部 Agent Runtime 的通用接入；任何框架集成都保持在 example/integration 边界。
- 评估远程 Artifact 存储、多用户协作或更丰富的可视化；不以此重写当前只读 Console。

## 不在当前 v1.3 范围内

- LLM 自动根因分析。
- 让 Behavior Diff 或 Capability 直接参与 Gate。
- LangGraph、MCP、Multi-Agent 等框架专属核心分支。
- Trace Graph/DAG 重写或前端框架迁移。

任何进入实现阶段的条目都必须先说明：是否影响冻结契约、是否需要 schema 版本提升、历史 Artifact 如何读取，以及新增的离线与端到端验收。
