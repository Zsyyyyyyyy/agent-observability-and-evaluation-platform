# Contributing

Regression Lab 的核心目标是让 Agent 版本结论可复现、可追溯、默认拒绝不完整证据。贡献应直接服务于这条主线，避免引入没有真实 Consumer 的 Schema、框架特判或控制面抽象。

## 本地验证

要求 Python 3.11、Git；Docker 仅用于容器集成验证。

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
make test
make manifest-check
```

涉及 Sandbox、网络策略或 Failure Probe 时还应运行：

```bash
make docker-test
make failure-suite
```

这些验证不调用真实模型。测试不得依赖 API Key、网络响应或未提交的 `.runtime/` Artifact。

## 变更纪律

- Trace、Result、Protocol、Gate、Attempt 或 Adapter Capability 的语义变化必须同时包含失败反例测试和对应契约文档更新。
- 缺少证据时使用 `None` / `not_available`，不得补成零或成功状态。
- Agent 自报字段不能覆盖平台拥有的 Trial 身份、测试、Git Evidence、Score 或 Gate 结论。
- 新的 Experiment 证据应能通过 `regression-lab experiment verify --runtime <runtime>`。
- 不提交密钥、完整本地 `.runtime/`、临时 Worktree 或包含用户绝对路径的演示包。

提交前运行 `git diff --check`，并在 PR 中说明验证命令、未覆盖环境和兼容性影响。
