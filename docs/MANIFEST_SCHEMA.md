# Benchmark Case Manifest v1

Manifest 是 Case 的不可变输入，负责描述 Fixture、任务、执行预算、工具策略和验收条件。当前校验器支持 JSON，以及无需额外依赖的本地 YAML 子集。

必填结构：

- `schema_version`、`id`、`version`、`title`
- `fixture.path`、`fixture.test_command`
- `task.prompt`、`task.allowed_paths`、`task.forbidden_paths`
- `execution.timeout_seconds`、`execution.max_tokens`、`execution.max_tool_calls`、`execution.trials`、`execution.network`
- `tool_policy.allow`、`tool_policy.deny`
- `evaluators.required`、`acceptance.must`

任务展开后，每个 Trial 都会获得稳定的 `job_id`、Fixture 路径、测试命令、路径策略、预算和 Sandbox 配置。执行入口：

```bash
# 只校验并查看 Case × Trial 展开结果
python3.11 scripts/run_benchmark.py \
  --manifest benchmarks/smoke-case-design.yaml \
  --trials 2 \
  --dry-run

# 实际执行并写入每个 Trial 的 Trace、Result、Diff 和 Score
python3.11 scripts/run_benchmark.py \
  --manifest benchmarks/smoke-case-design.yaml \
  --output-dir .runtime/benchmark \
  --docker --bash
```

输出目录包含每个 Job 的独立 Worktree 和证据文件，以及聚合的 `summary.json`、`runs.db` 和 Score 记录。
