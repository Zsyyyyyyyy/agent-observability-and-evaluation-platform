# Experiment Schema v0

Experiment 在同一组 Benchmark Case 上比较多个 Agent Version。当前入口使用：

```bash
python3.11 scripts/run_experiment.py \
  --manifest benchmarks/smoke-case-design.yaml \
  --output-dir .runtime/experiment \
  --agents baseline:readonly-replay-v1,candidate:legacy agent-candidate-replay-v1 \
  --trials 3 \
  --docker --bash
```

`--manifest` 可以重复传入多个 Case；Experiment 会按 `Case × Agent × Trial` 展开任务。

每个 Agent 都拥有独立输出目录和 SQLite Run Store。对比报告会先按多个 Case 合并，再记录：

- 完成率、评测通过率、测试通过率，以及 `model_failed`、`trace_incomplete`、`infra_failed` 可靠性失败率。
- 平均工具调用次数和 Agent 耗时。
- 平均 Diff 增删行数。
- Candidate 相对 Baseline 的 delta 和 improved/regressed/unchanged 分类。

加上 `--resume` 后，只有状态 `completed`、评测通过且 Trace 合法的 Job 才会复用；不完整 Job 会重新创建 Worktree 后重跑。已完成但无效的 Job 默认保留为证据；使用 `--rerun-invalid` 才会先归档旧 Attempt 后重跑。
