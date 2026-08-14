# Regression Lab 交付清单

> 范围：`study/Regression/`；定位：Coding Agent 可观测与评测平台（本地单机 MVP）。

## 已交付

- [x] Adapter 注册表：`readonly-replay` 只读接入示例与 `react-agent` 真实模型参考实现。
- [x] Manifest 驱动的 Case × Trial × Agent Version 执行入口，支持安全恢复。
- [x] 每个 Trial 使用独立 Worktree，并收集 Git Diff 与修改文件。
- [x] Docker Tool Sandbox：默认禁网、只读根文件系统、capability drop、CPU/内存/PID 限制、超时清理。
- [x] `react-agent` 在写入/编辑工具调用时即执行 Allowed/Forbidden Path 拦截；结束后仍由 Evaluator 二次核验 Git Diff。
- [x] JSONL Trace：覆盖 Agent 运行、模型调用、权限检查、工具调用、测试与上下文压缩；含 Schema 校验。
- [x] SQLite（权威数据）+ JSONL（审计追加）Run Store，并具备故障恢复测试。
- [x] 六类 Evaluator：测试、Diff、路径策略、Trace 完整性、工具完整性、预算。
- [x] 8 个确定性 Python 修复 Case，均在独立 Fixture 中验证。
- [x] Baseline/Candidate 实验汇总：完成率、评测通过率、测试通过率、耗时、工具调用、Token 与 Diff。
- [x] 可执行版本晋级 Gate：正确性、可靠性与带阈值的效率规则，支持机器可读报告和非零退出码阻断。
- [x] `react-agent-v1/v2` 真实对照：各 8/8 通过；已诚实记录延迟/成本权衡。
- [x] 外接 Agent `external-openai-v2/v3` 真实演进实验：3 Case × 3 Trial，复用同一 Gate 并写入 Evolution Catalog。
- [x] Evolution Timeline 能按严格 benchmark fingerprint 标记 v2 → v3 可比，并保留 Gate Decision 与父子版本关系。
- [x] 阶段 7 统计可信度增强：扩展 v2/v3 至 8 Case × 3 Trial × 2 Version，输出 Case 聚类 Bootstrap 95% 区间、Case 胜负和克制结论。
- [x] 零依赖本地只读控制台，可查看汇总、Trial、Trace、工具调用、Diff 与实验报告。
- [x] 一键命令：`make test`、`make docker-test`、`make manifest-check`、`make real-smoke`、`make console`。
- [x] 独立仓库发布时可用的 GitHub Actions 校验工作流；CI 不使用模型密钥、不发起真实模型调用。
- [x] README、环境变量模板、5 分钟演示脚本、简历与面试材料。
- [x] 模型失败、禁止路径写入和 Docker 超时均有可测试的失败状态与 Trace/Evidence 说明。

## 已验证

| 检查 | 结果 | 执行方式 |
|---|---:|---|
| 核心单元测试 | 42 通过；默认跳过 3 个 Docker 集成测试 | `make test` |
| Docker 集成测试 | 3/3 通过 | `make docker-test` |
| Manifest 校验 | 8/8 可展开 | `make manifest-check` |
| 真实 Agent 对照 | v1/v2 各 8/8 通过 | 见 `docs/EXPERIMENT_REPORT_CORE_V1.md` |

## 未交付（刻意不夸大）

- [ ] 将当前失败语义扩展为可由 Manifest 驱动的专用失败基准，并纳入 Experiment 对比报告。
- [ ] 多用户、鉴权、远程队列、云端 Artifact 存储和大规模调度。
- [ ] 生产级 CI Artifact 上传、JUnit/HTML 报告与远程 Trace 后端。
- [ ] 演示视频/GIF（目前提供可复现的文字演示脚本）。

## 发布前验收

- [ ] 将 `study/Regression` 作为独立 Git 仓库根目录发布，确保 `.github/workflows/verify.yml` 能被 Actions 识别。
- [ ] 在干净环境执行 `make test && make docker-test && make manifest-check`。
- [ ] 配置自己的模型环境变量后，以新的输出目录执行一次 `make real-smoke`。
- [ ] 对照 `docs/DEMO_SCRIPT.md` 完成一次录屏，并将 README 中的运行截图替换为本机结果。
- [ ] 运行至少 3 Trial/Case 的真实对照实验后，更新实验报告和简历中的量化结论。
