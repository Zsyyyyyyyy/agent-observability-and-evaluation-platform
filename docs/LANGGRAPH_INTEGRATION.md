# LangGraph external-command integration

This example proves the framework boundary without adding a LangGraph Adapter.
`examples/langgraph_coding_agent.py` imports LangGraph; `src/regression_lab` does not.

The graph is `START -> Planner -> Coder -> Verifier -> END`. Each node emits a
generic `workflow` Span, while its deterministic planning, coding and verification
steps emit existing `llm` and `tool` Spans through `AgentObserver`.

The integration declares this capability snapshot through the existing
`external-command` invocation:

```json
{"schema_version":2,"trace":true,"hierarchical_trace":true,"model_usage":true,"tool_trace":true,"tool_semantics":true,"test_trace":false,"context_trace":false,"workflow_trace":true,"mcp_trace":false}
```

`langgraph-agent-v1` performs one redundant identical `read_file` in Coder;
`langgraph-agent-v2` reuses the first read. The repair, model usage and verifier
are otherwise identical. `make langgraph-integration` creates a small 3-Case,
3-Trial-per-version experiment in `.runtime/langgraph-v1-v2-integration-v2`.

The failure probe uses agent version `langgraph-agent-failure-probe`. It issues
an unauthorized `remove_worktree` tool Span inside Coder, allowing deterministic
policy attribution to that Span without any root-cause inference.
