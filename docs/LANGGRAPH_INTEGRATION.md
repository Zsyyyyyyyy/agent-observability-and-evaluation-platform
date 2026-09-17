# LangGraph external-command integration

LangGraph 模式只在 Graph 的启动点接入一次 Callback：

```python
from regression_lab_observer.langgraph import LangGraphObserver

with LangGraphObserver.from_environment() as observation:
    graph.invoke(inputs, config={"callbacks": [observation.callback]})
```

平台将框架原生 Callback 映射到既有 JSONL Trace：Graph Node 为 `workflow`，
LangChain 模型与 Tool 调用分别为 `model.call`、`tool.call`。它只保存模型名、
Token、状态和工具名，不保存 Prompt、响应正文、工具参数或工具输出正文。

`examples/langgraph_coding_agent.py` 的节点、文件操作和业务状态不再依赖
`AgentObserver`。该离线示例直接使用 Python 文件操作，所以 Callback 只会得到
Workflow Span；真实 Agent 通过 LangChain Model/Tool 执行时，框架会自动提供模型
和工具 Span。不能把任意直接 Python 调用伪装成已观测的 Tool Trace。

`langgraph-agent-v1` 保留一次冗余读取，v2 复用首次读取。运行：

```bash
make langgraph-integration
```

它会创建 3 Case × 3 Trial × 2 版本的离线 Experiment 到
`.runtime/langgraph-v1-v2-integration-v5`，不调用真实模型。
