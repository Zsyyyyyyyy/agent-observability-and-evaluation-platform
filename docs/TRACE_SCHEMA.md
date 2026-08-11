# Trace Schema v0

Regression Lab 当前使用依赖无关的 JSONL Trace。每行是一个事件，最小公共字段为：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `trace_id` | string | 同一 Trial 内保持不变 |
| `event_seq` | integer | 严格递增 |
| `ts` | number | Unix 时间戳 |
| `kind` | enum | `span_start`、`span_end` 或 `event` |

`span_start` 必须包含 `span_id`、`name` 和 `attributes`；`span_end` 必须包含同名 `span_id`、`status` 和 `attributes`。每个 Span 必须恰好有一个结束事件。普通 `event` 必须包含 `name`，并可通过 `parent_span_id` 关联 Span。

校验入口：

```python
from regression_lab.schema import validate_trace

validation = validate_trace("trace.jsonl")
assert validation.valid, validation.errors
```

## Run Store v0

`RunStore` 以 SQLite 为事实来源：Trial 与完整 Score 集合在一个事务中写入，同时在事务内登记 JSONL 审计 Outbox。事务提交后才投递 JSONL；投递失败会保留未投递记录，由下次启动自动重试。

JSONL 采用至少一次投递，每条记录都有稳定的 `audit_id`；消费端应按该字段去重。这样即使进程在“写入 JSONL”与“确认投递”之间崩溃，也不会丢失 SQLite 中的结果。

- SQLite 用于按 `trial_id`、`status` 查询。
- JSONL 用于调试、迁移和离线重放。
- 同一 `trial_id` 重复写入时 SQLite 使用 upsert，JSONL 保留每次写入记录。

Worker 在写出 Result 前完成 Trace 校验和 Run Store 写入；Trace 不完整会被标记为 `trace_incomplete`，存储失败会被标记为 `infra_failed`。
