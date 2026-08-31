# Trace Schema v1

Regression Lab 当前使用依赖无关的 JSONL Trace。每行是一个事件，最小公共字段为：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `trace_id` | string | 同一 Trial 内保持不变 |
| `event_seq` | integer | 严格递增 |
| `ts` | number | Unix 时间戳 |
| `kind` | enum | `span_start`、`span_end` 或 `event` |

`span_start` 必须包含 `span_id`、`name` 和 `attributes`；v1 新增 `span_type`，取值为 `agent`、`llm`、`tool`、`test`、`retrieval`、`context`、`workflow`、`mcp` 或 `other`。`span_end` 必须包含同名 `span_id`、`status` 和 `attributes`。每个 Span 必须恰好有一个结束事件；一个 Trace 至多只能有一个根 Span。普通 `event` 必须包含 `name`，并可通过 `parent_span_id` 关联 Span。

`attributes` 必须是 JSON Object。父 Span 必须已经开始且尚未结束；存在未关闭子 Span 时不能先关闭父 Span，结束后的 Span 也不能再接收子 Span或 Event。校验器把这些因果顺序错误标记为不完整证据，而不是让下游 Evaluator 尝试解释畸形 Trace。

## 嵌套 Span

SDK 使用当前上下文自动关联父 Span：

```python
with observer.run():
    with observer.span("agent.step", "agent"):
        with observer.model_call(model="example-model"):
            pass
        with observer.tool_call("edit_file"):
            pass
```

上述调用会形成 `agent.run → agent.step → model.call/tool.call` 的树。`model_call()` 与 `tool_call()` 分别是 `span(..., "llm")`、`span(..., "tool")` 的语法糖。

## v0 兼容性

旧 Trace 可以没有 `span_type`，仍然合法。读取方会按名称前缀推断类型：`agent.`、`model.`/`llm.`、`tool.`、`test.`、`retrieval.`、`context.`、`workflow.`、`mcp.`；不能推断时视为 `other`。因此既有实验报告和 Gate 无需迁移。

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
