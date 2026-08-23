# Attempt 执行与恢复契约

## 目标

一个 Trial 是固定 `Case × Agent Version × trial_index` 的逻辑评测单元；
一个 Attempt 是该 Trial 的一次物理执行。重试必须创建新 Attempt，绝不能复用
旧 Attempt 的 Trace、Worktree 或 Agent 输出路径。

## 目录布局

```text
<job_id>/
  run-manifest.json
  result.json                 # 当前 selected Attempt 的兼容投影
  selected-attempt.json
  attempts/
    attempt_001/
      attempt-manifest.json
      trial-input.json
      trace.jsonl
      agent-output.json
      result.json
      worktree/
```

根目录 `result.json` 保持给现有 Experiment Report 和 Web Console 使用；真实
证据在 `attempts/attempt_NNN/`。Console 有意忽略嵌套 Attempt 的 `result.json`，
因此不会将同一逻辑 Trial 重复计入统计。

## 生命周期

`running → completed | timed_out | invalid | aborted`

- `completed` 只表示物理运行已形成 Result；是否通过仍由 Result 的 Evaluator 和
  Trace Validation 决定。
- `invalid` 用于 Trace 不完整等不可信结果。
- `timed_out` 表示 Agent、测试或父 Runner 超过 deadline。
- `aborted` 预留给后续的进程崩溃恢复检测。

每个 Attempt 的 manifest 记录 Job fingerprint、开始/结束时间、终态和可选错误，
用于确认 Artifact 的归属；终态还记录不可变 `result.json` 的 SHA-256。发布或演示前可运行
`regression-lab experiment verify --runtime <runtime>`，核对 Protocol、执行计划、选中
Attempt、Result 投影、Trace、Agent 源码身份与 Gate 证据关联。

## Resume 规则

- `--resume` 仅复用 `completed`、评测通过且 Trace 合法的选中结果。
- 已完成但评测不通过的结果默认保留并作为此次实验结论，不会静默重跑。
- `--resume --rerun-invalid` 会创建下一个 `attempt_NNN`；旧 Attempt 保留在原目录。
- 非完成的既有结果会创建新的 Attempt，旧 Artifact 不会被删除或移动。

## 进程边界

父 Runner 和外部 Agent 分别位于独立 process group。到达 deadline 时先发送
`SIGTERM`，短暂等待后发送 `SIGKILL`；只针对该 session leader 的 process group。
此外，新的 Attempt 始终生成新的 Trace/输出/Worktree 路径，所以异常残留进程最多
写入自己的旧目录，不能污染新 Attempt。

## Run Store

SQLite 的 `trials` 表继续存储当前选中的逻辑 Trial；`attempts` 表按
`(trial_id, attempt_id)` 保留每次物理执行。Audit JSONL 也记录 `attempt_id` 与
`selected`，供后续 Evolution Timeline 消费。
