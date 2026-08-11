# Evaluator Schema v0

每个确定性评测器接收一个结构化 Trial Result，返回一个 `Score`：

| 字段 | 含义 |
| --- | --- |
| `evaluator` | 评测器稳定名称 |
| `passed` | 是否通过 |
| `actual` | 实际观测值 |
| `expected` | 阈值或期望值 |
| `message` | 面向报告的短结论 |
| `evidence` | 可追溯证据，例如测试输出、违规路径和 Trace ID |

当前 Baseline 包含三个评测器：

- `test`：检查测试命令退出码，并解析运行、失败、错误和跳过数量。
- `path_policy`：检查修改文件是否全部位于 Allowlist，且不触碰 Denylist。
- `trace_completeness`：检查 Trace Schema 校验是否通过。
- `diff`：统计文件数、增删行，检测空修改、过大 Diff 和二进制变化。
- `tool_integrity`：检查工具 Span 是否成对闭合，并检测未授权工具。
- `budget`：检查工具调用次数和 Agent 根 Span 耗时是否超过预算。

聚合结果保存在：

```json
{
  "passed": true,
  "scores": ["..."]
}
```

评分会随 Trial Result 写入 SQLite `scores` 表，同时保存在 Result JSON 中，保证报告可以从 Trial 反向定位到原始 Evidence。
