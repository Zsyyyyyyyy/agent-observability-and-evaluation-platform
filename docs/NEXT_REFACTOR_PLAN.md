# 后续改造规划

## P0-1：Hierarchical Trace Schema v1

### 为什么第一个做

你现在的 Trace 已经有：

```text
trace_id
span_id
parent_span_id
span_start
span_end
event
```

而且可以验证 Span 是否正确关闭。

所以不需要推翻。

真正的问题是：当前观测主要还是围绕：

```text
agent.run
├── model.call
├── tool.call
├── model.call
└── tool.call
```

Tool 行为分析也主要根据 `tool.call` 去扫描和汇总。

下一步要让 Trace 能表达：

```text
agent.run
│
├── model.call
│
├── agent.step
│   ├── model.call
│   └── tool.call
│
├── agent.step
│   ├── tool.call
│   │   └── mcp.call
│   └── test.run
│
└── agent.finalize
```

### 具体改造

1. 增加统一 Span API

现在不要继续不断增加：

```python
model_call()
tool_call()
xxx_call()
yyy_call()
```

增加底层：

```python
observer.span(
    name="agent.step",
    span_type="agent",
    **attrs
)
```

然后：

```python
observer.model_call(...)
observer.tool_call(...)
```

只是语法糖。

2. 使用 ContextVar 维护当前 Span

实现：

```python
current_span
```

于是：

```python
with observer.run():

    with observer.span("agent.step"):

        with observer.model_call(...):
            ...

        with observer.tool_call(...):
            ...
```

自动形成：

```text
agent.run
└── agent.step
    ├── model.call
    └── tool.call
```

不用用户手动传：

```text
parent_span_id
```

3. Trace v1 加 `span_type`

建议有限枚举：

```text
agent
llm
tool
test
retrieval
context
workflow
mcp
other
```

不要设计几十种。

事件仍然保持：

```json
{
  "kind": "span_start",
  "name": "tool.call",
  "span_type": "tool"
}
```

这样原有 `name` 逻辑不需要废掉。

4. 保持 v0 向后兼容

非常重要。

不要改成：

```text
旧实验全部读不了了
```

规则：

```text
没有 span_type
→ 根据 name 推断
→ 推不出来就是 other
```

旧的：

```text
agent.run
model.call
tool.call
```

照样能解析。

### 涉及文件

主要动：

```text
src/regression_lab/trace.py
src/regression_lab/schema.py
src/regression_lab/sdk.py
docs/TRACE_SCHEMA.md

tests/test_schema.py
tests/test_trace_hierarchy.py
tests/test_sdk_nested_span.py
```

其中：

```text
tests/test_trace_hierarchy.py
tests/test_sdk_nested_span.py
```

为新增测试。

### P0-1 验收标准

完成后必须证明：

```text
旧 Trace             ✓ 仍然合法
旧实验报告           ✓ 不变
旧 Gate              ✓ 不变

嵌套 Span            ✓
自动 parent           ✓
异常时关闭 Span       ✓
Span Tree 可重建      ✓
错误 parent 能检测    ✓
多个 root 能检测      ✓
```

这一阶段不要做漂亮前端。

先把证据模型做稳。

## P0-2：Cross-Version Behavior Diff

这是整个后续改造中最重要的一步。

也是让你的项目不沦为“小 LangSmith”的关键。

### 目标

现在你主要回答：

```text
V3 vs V4.1

Pass Rate
Token
Latency
Tool Calls
```

升级以后回答：

```text
为什么 V4.1 比 V3 Token 更少？具体行为发生了什么变化？
```

### 第一步：Trace Normalization

新增：

```text
src/regression_lab/behavior_diff.py
```

先把每个 Trial 转换成统一行为：

```text
TrialBehavior
```

例如：

```json
{
  "model_calls": 4,
  "tool_calls": 6,
  "tools": {
    "read_file": 3,
    "edit_file": 1,
    "test": 2
  },
  "duplicate_reads": 2,
  "retries": 1,
  "tokens": 16000,
  "duration_ms": 32000
}
```

注意：

你现在 `behavior.py` 已经计算 Tool 成功率、duplicate read、repeated tool call 等，因此直接复用，不重新实现。

### 第二步：Pair Baseline / Candidate

你现在已经有：

```text
Case
+
trial_index
```

配对逻辑。

所以：

```text
Case A / Trial 1

V3      ↔     V4.1
```

直接生成：

```text
BehaviorDelta
```

例如：

```json
{
  "model_calls_delta": -2,
  "tool_calls_delta": -3,
  "duplicate_reads_delta": -2,
  "token_delta": -11660,
  "duration_delta_ms": -11210
}
```

### 第三步：增加“行为模式 Diff”

这是最有价值的地方。

不仅比较数字：

```text
tool_calls 8 → 5
```

还比较：

```text
发生了什么变化
```

例如归一化行为序列：

Baseline:

```text
model
read
read
model
read
edit
test
model
```

Candidate:

```text
model
read
model
edit
test
```

得到：

Removed:

```text
read → read
read
terminal → model
```

注意不要做代码字符串 diff。

LLM Agent 本身有随机性。

所以不是：

```text
SequenceMatcher 逐行比较
```

而是做：

```text
Semantic Behavior Pattern
```

例如：

```text
duplicate_read
repeated_tool
post_terminal_model_call
tool_retry
test_retry
model_retry
denied_tool
failed_tool
```

最终：

```json
{
  "removed_patterns": [
    {
      "pattern": "post_terminal_model_call",
      "count_delta": -2
    },
    {
      "pattern": "duplicate_read",
      "count_delta": -3
    }
  ]
}
```

这样更稳定。

### 第四步：Case 级聚合

例如：

```text
11 Cases × 3 Trials
```

最终：

```text
duplicate read:

10 Cases improved
1 unchanged
0 regressed

extra model calls:

11 improved
0 unchanged
0 regressed
```

再和已有 Bootstrap / Case Comparison 结合。

你的当前指标体系本身已经支持按 Case 配对和 Bootstrap，因此这部分可以在现有体系上继续扩展，而不是造第二套统计框架。

### 第五步：写进 Experiment

建议：

```json
{
  "behavior_diff": {
    "summary": {},
    "case_diffs": [],
    "availability": {},
    "evidence_refs": []
  }
}
```

注意：

Behavior Diff 首版不要参与硬 Gate。

先做：

```text
diagnostic evidence
```

而不是：

```text
duplicate read 减少 20% → PROMOTE
```

否则非常容易过度设计。

## P1-1：Span-level Failure Attribution

你现在已经有 Failure Attribution。

当前能够判断：

```text
model
infrastructure
evidence
policy
agent
```

以及：

```text
path_policy_violation
budget_exceeded
test_failed
...
```

这是 Trial 级归因。

不要重新写。

下一步升级成：

```text
Failure Category
        ↓
Failure Span
        ↓
Agent / Tool
        ↓
Evidence
```

例如：

```json
{
  "kind": "policy",
  "reason": "path_policy_violation",

  "failure_span_id": "span_0021",

  "span_type": "tool",
  "span_name": "tool.call",

  "tool_name": "edit_file",

  "agent_id": "coder"
}
```

于是：

```text
Trial Failed
    ↓
Policy Violation
    ↓
Coder Agent
    ↓
edit_file
    ↓
span_0021
```

### 具体实现

保留：

```python
attribute_trial()
```

增加：

```python
attribute_failure_span()
```

不要混成一个巨型函数。

最终：

```python
FailureAttribution(
    category,
    reason,
    span_id,
    evidence
)
```

## P1-2：Adapter Capability Contract v2

这一步是为真正接入其他 Agent 框架准备的。

你当前 Adapter Registry 已经存在，并支持 external-command 等接入方式，所以不需要重做 Adapter 层。当前平台本身就明确定位为不绑定单一 Agent 框架。

需要升级的是：

```text
平台怎么知道这个 Agent 能提供哪些证据？
```

增加 capabilities。

例如：

```json
{
  "trace": true,
  "nested_trace": true,

  "model_usage": true,
  "tool_trace": true,

  "workflow_trace": false,
  "context_trace": false,
  "mcp_trace": false
}
```

这样 Evaluator 就知道：

```text
context.compaction_count = 0
```

到底代表：

```text
真的没有发生
```

还是：

```text
这个 Adapter 根本观测不到
```

这非常重要。

## P1-3：接入一个真正的外部框架 Agent

这一项非常关键。

否则面试官很容易问：

```text
你说框架无关，但是目前实际上是不是只有自己写的 external_openai_agent？
```

所以必须做一个证明性集成。

我建议第一选择 LangGraph。

不是 fast-agent。

原因是：

```text
你更熟
规模可控
容易做 Workflow
面试认知度高
```

做一个极小但真实的：

```text
LangGraph Coding Agent

START
 ↓
Planner
 ↓
Coder
 ↓
Tester
 ↓
END
```

不要打造新项目。

只作为：

```text
examples/langgraph_agent/
```

接入方式：

不要让平台依赖 LangGraph。

应该：

```text
LangGraph Agent
      ↓
Regression SDK
      ↓
external-command
      ↓
JSONL Contract
```

平台：

```text
完全不知道 LangGraph
```

这反而证明 Adapter 架构是成立的。

最终能够展示：

```text
内置 ReAct Agent
        +
LangGraph Workflow
        +
任意 Python Agent

全部进入同一：

Benchmark
Trace
Evaluator
Experiment
Console
```

这项对简历价值很大。

## P1-4：Console 做 Behavior Diff，而不是继续模仿 LangSmith

这里很重要。

先不要做：

```text
巨大漂亮 Trace DAG
```

因为非常像 LangSmith，而且投入高。

优先做一个页面：

```text
Baseline vs Candidate Behavior
```

例如：

```text
Case: dependency-cycle

            V3        V4.1       Delta

Pass        ✓          ✓
Tokens      17600      5940       -66%
Latency     31.2s      19.8s      -36%
Models      6          4          -2
Tools       8          5          -3
Dup Reads   3          0          -3
Retries     1          0          -1
```

下面：

```text
Behavior Changes

✓ Removed 2 duplicate reads
✓ Removed 1 redundant model call
✓ Removed post-terminal model call
- No new denied tools
- No new tool failures
```

然后点：

```text
Evidence
```

进入已有 Trace Inspector。

这就够了。
