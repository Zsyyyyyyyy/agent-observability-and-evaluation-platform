# Attempt Selection Contract

## 事实源

```text
immutable attempts/<attempt_id>/result.json
        ↓
selected-attempt.json
        ↓
job result.json / SQLite / JSONL / Experiment Summary / Catalog / Dashboard
```

每个 Attempt 的目录、Trace、Result 和 manifest 均是保留的执行证据。平台只在 `selected-attempt.json` 中记录 Trial 当前投影；其他存储不能自行选择或重排 Attempt。

## 当前选择策略

`latest_terminal_attempt_v1`：一个新 Attempt 达到终态后，平台选择最新、可读的终态 Attempt，并写入：

- `attempt_id`；
- `selection_policy`；
- `selection_reason`；
- `attempt_count`；
- Protocol fingerprint 和 schedule index（如果存在）。

因此，Retry 后的模型或基础设施失败不会被此前 Pass 静默掩盖。所有 Attempt 仍保留，后续可靠性分析可以明确区分 Trial 当前结果与 Attempt 历史。

## 投影规则

- Runner 在 `selected-attempt.json` 写入后才同步 SQLite / JSONL。
- Catalog 读取 `selected-attempt.json`，不再通过“最后目录”或 Result 字段猜测选择项。
- Dashboard 读取 Job-level `result.json`；该文件由同一个 Artifact selector 发布。
- 早于该契约的 Artifact 没有选择投影时，只保留只读兼容回退；不改写历史实验证据。

## 重建

SQLite、JSONL、Catalog 和 Dashboard 都是可重建投影。删除任意索引不应改变 Immutable Attempt Artifact 和 `selected-attempt.json`；重建后必须得到同一个 Attempt ID 和 Trial 结论。
