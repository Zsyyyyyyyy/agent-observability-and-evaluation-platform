# 5 分钟演示脚本

## 演示目标

展示的不是“让模型写一次代码”，而是一条可复核的 Agent 工程闭环：任务定义 → 隔离执行 → Trace/Score → 多版本对比 → 问题追溯。

## 0:00–0:40：任务与边界

打开 `benchmarks/safe-slug-case.yaml`，说明 Manifest 固化了 Prompt、允许/禁止文件、工具权限、Docker 资源预算和验收测试。每次 Trial 从同一 Fixture 起步，不污染彼此。

## 0:40–1:30：安全执行

运行：

```bash
make docker-test
```

说明这不是“用 Docker 跑一下测试”：容器网络默认关闭、根文件系统只读、能力被移除、资源受限；Docker 不可用时 Benchmark 默认失败，而不是退回宿主机。

## 1:30–2:20：真实 Agent Trial

已经配置模型环境变量时运行：

```bash
make real-smoke REAL_OUTPUT_DIR=.runtime/demo-react-smoke
```

展示产物目录中的 `result.json`、`trace.jsonl`、`runs.db`。强调密钥不进入任一 Artifact；模型失败会被记录为 `model_failed`，不会偷偷使用 Replay。

## 2:20–3:40：观测与定位

运行：

```bash
make console RUNTIME=.runtime/core-experiment-v1
```

在控制台依次展示：通过率与成本汇总 → 任一 Trial 的 Trace 时间线 → 工具调用 → Git Diff → Score Evidence。解释“为什么通过/为什么失败”可由这些证据回答。

## 3:40–5:00：实验结论

打开 `docs/EXPERIMENT_REPORT_CORE_V1.md`：8 个修复任务中 v1/v2 都是 8/8；v2 平均耗时 -19%，但工具调用和 Token 上升。结论是延迟/成本权衡；下一步用至少三次 Trial 报中位数和方差，而不是把一次结果包装成优化。
