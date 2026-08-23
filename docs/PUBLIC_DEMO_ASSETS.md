# 发布只读 Demo Artifact

仓库包含两个脱敏、可校验的离线 Demo：

- `demo/instrumented-v3-v4-1`：默认演示，包含模型/工具父子 Trace、Comparison、Failure Attribution 和 `PROMOTE` Gate；
- `demo/standalone-langgraph-v1-v2`：本机现有 LangGraph v1/v2 的黑盒接入结果，包含 `HOLD` Gate 和 3 次非零 Agent Failure Attribution。

```bash
make verify
make offline-demo
make offline-demo DEMO_RUNTIME=demo/standalone-langgraph-v1-v2 CONSOLE_PORT=8766
```

它们不包含原始 Attempt、Worktree、模型密钥或本机绝对路径。`make verify` 会分别根据 `demo-manifest.json` 校验 137 个 instrumented 文件和 17 个 LangGraph 文件；完整 Experiment Runtime 的 Protocol、Attempt 和 Trace 证据链仍应使用 `make verify-runtime RUNTIME=<path>` 验证。

本项目的 `.runtime/` 是本机运行产物，不能直接提交或上传：它包含 Attempt、临时 Worktree、本机绝对路径，体积也不适合 Release。

使用导出器生成可公开分享的只读 Demo。它只保留 Console 读取的 Experiment 报告、选中 Trial 的 `result.json` 与 `trace.jsonl`；会移除 Attempt/Worktree，并将本机路径和常见 API Key 形式替换为脱敏值。

```bash
PYTHONPATH=src:. python3.11 scripts/export_demo_runtime.py \
  --source .runtime/demo/external-openai-v3-v4-1-regression \
  --catalog .runtime/demo/coding-agent-platform/evolution-catalog.json \
  --output /tmp/regression-lab-demo-promote

PYTHONPATH=src:. python3.11 scripts/export_demo_runtime.py \
  --source .runtime/demo/external-openai-v3-negative-control \
  --catalog .runtime/demo/coding-agent-platform/evolution-catalog.json \
  --output /tmp/regression-lab-demo-hold
```

输出目录必须不存在，避免误覆盖已有 Release Asset。每个包根目录包含 `demo-manifest.json`，列出导出文件及 SHA-256；发布前应复查其中没有不应公开的 Prompt、源码或业务数据。

使用者解压后可直接打开对应包：

```bash
make console RUNTIME=/path/to/regression-lab-demo-promote
```

正向 Demo 必须保留 `gate-report.json` 的 `PROMOTE` 事实；负向 Demo 保留 `gate-negative.json` 的 `HOLD` 事实。两者不可混合或重写结论。
