# Evaluation Metrics v3

> 状态：核心聚合与行为诊断已实现。本文冻结 Coding Agent 版本对比的指标口径；不改变既有 Trial 证据链，也不把 LLM Judge 作为首版晋级的唯一依据。

## 1. 目标与原则

Regression Lab 的结论不是“哪个版本平均 Token 更少”，而是：候选 Coding Agent 是否在真实代码任务上**正确、稳定、可审计且成本可控**。

- 平台运行测试、采集 Git Diff、校验 Trace；Agent 自报结果不作为权威证据；
- 所有 Gate 指标必须可复现、可由 Artifact 独立重算；
- LLM Judge 只提供行为诊断，v2.0 不作为硬 Gate；
- 小样本重复实验优先报告 Pass@k、波动和逐 Case 差异；配对指标使用按 Case 聚类的 Bootstrap 95% 区间，不把 p-value 当作结论；
- 指标缺失必须显式标为 `not_available`，不得伪造为 0 或通过。

## 2. 术语与统计范围

| 术语 | 定义 |
|---|---|
| Trial | 一个 Agent 版本在一个 Case 上的一次独立执行。 |
| Case group | 相同 `case_id`、相同版本的全部重复 Trial。 |
| 有效通过 | `status=completed`、平台 `evaluation.passed=true`、Trace 校验有效。 |
| 有效样本 | 已产生可解析 Result 的 Trial；`infra_failed` 仍计入可靠性统计。 |
| 对照 | Baseline 与 Candidate 在同一 `case_id` 和 `trial_index` 上的配对结果。 |

默认首轮实验为 3 个 Case × 3 次重复 × 2 个版本，即 18 个 Trial。只有当每个版本每个 Case 都有至少 3 个有效 Result 时，才计算 all-pass@3 与 Flaky Case Rate。

## 3. 指标目录

### 3.1 Gate 硬指标

| 指标 | 公式 | 方向 | Gate 规则 | 现有数据 |
|---|---|---:|---|---|
| Evaluation pass rate | 有效通过 / 全部 Trial | 高 | 不低于 Baseline | 已有 |
| All-pass@3 consistency | 同一 Case group 的 3 次 Trial 全部有效通过的 Case 数 / 可计算 Case 数 | 高 | 不低于 Baseline | 新聚合 |
| Flaky case rate | 同一 Case group 同时出现有效通过和失败的 Case 数 / 可计算 Case 数 | 低 | 不高于 Baseline | 新聚合 |
| Model failed rate | `model_failed` / 全部 Trial | 低 | 不高于 Baseline | 已有 |
| Trace incomplete rate | `trace_incomplete` / 全部 Trial | 低 | 必须为 0 | 已有 |
| Infra failed rate | `infra_failed` / 全部 Trial | 低 | 必须为 0 | 已有 |
| Path-policy violation rate | `path_policy.passed=false` / 全部 Trial | 低 | 必须为 0 | 已有 Score |
| Diff-policy violation rate | `diff.passed=false` / 全部 Trial | 低 | 必须为 0 | 已有 Score |

`completed` 但测试或其他平台 Evaluator 失败的 Trial 计为失败；Gate 不接受 Agent 写入 Result 的自报 `evaluation_passed`。

### 3.2 效率与成本指标

| 指标 | 公式 | 方向 | Gate 状态 | 数据来源 |
|---|---|---:|---|---|
| P50 latency | Trial `duration_ms` 的中位数 | 低 | 诊断 | 根 `agent.run` Span |
| P95 latency | nearest-rank P95(`duration_ms`) | 低 | 诊断（不单独阻断） | 根 `agent.run` Span |
| P50 model tokens | Trial 总模型 Token 的中位数 | 低 | 诊断 | `model.call` 结束事件 |
| P95 model tokens | nearest-rank P95(总模型 Token) | 低 | 诊断 | `model.call` 结束事件 |
| Avg tool calls | Trial 工具调用数的算术平均值 | 低 | 有容忍阈值 | `tool.call` 开始事件 |
| Token cost estimate | Σ(input_tokens × input_price + output_tokens × output_price) | 低 | 诊断 | `model.call` Usage + 版本化价格表 |
| Cost per valid pass | 总成本估算 / 有效通过数；无通过为 `not_available` | 低 | 诊断 | 以上聚合 |

P95 使用 nearest-rank：对升序样本数 `n`，取索引 `ceil(0.95 × n)`（从 1 开始）。首轮样本很少，P95 仅用于暴露慢 Trial，不作为统计显著性结论。

成本以 `USD` 表示，价格必须来自提交到仓库的 `configs/model-pricing.json`，并写入 `experiment.json` 的 `pricing_version`。未知模型或缺少 Usage 时成本为 `not_available`，不可默认为 0。

### 3.3 Coding Agent 行为诊断指标

| 指标 | 公式/判定 | 方向 | 前置条件 |
|---|---|---:|---|
| Tool success rate | `tool.call` 结束状态为 `ok` / 已结束 Tool Call | 高 | 完整 Tool Span |
| Tool error rate | 结束状态为 `error` / 已结束 Tool Call | 低 | 完整 Tool Span |
| Denied tool attempts | 结束状态为 `denied` 的数量 | 低 | 完整 Tool Span |
| Repeated tool-call rate | 同一 Trial 内、规范化指纹相同的第 2 次及以后调用 / 全部 Tool Call | 低 | `tool_name` + 脱敏 `argument_fingerprint` |
| Duplicate read rate | `read_file` 对同一路径的第 2 次及以后成功调用 / 成功 `read_file` 调用 | 低 | `tool_name` + `target_path` |
| Test retry count | `test.run` 的第 2 次及以后调用数 | 低 | `test.run` Span（SDK v2） |
| Verification coverage | 有效通过且至少有一个平台 Test Evidence 的 Trial / 有效通过 | 高 | 平台测试结果；无需相信 Agent 自报 |
| Edit-before-read flag | 首次 `edit_file` 早于该目标文件首次 `read_file` | 诊断 | `target_path` 字段 |

工具调用指纹只可由工具名、规范化后的参数键名和可公开的目标路径生成；不保存完整 Prompt、文件正文、Authorization Header、API Key 或工具原始参数。

### 3.4 LLM Judge 行为评分（后续阶段）

| 评分 | 判断范围 | Gate 状态 |
|---|---|---|
| Execution Efficiency | 无效循环、无意义重试、冗余步骤 | 诊断 |
| Tool Selection | 面对当前任务是否选择了合适工具 | 诊断 |
| Tool Calling | 参数是否合理、是否正确解释工具结果 | 诊断 |
| Plan Adherence | 有显式计划时，执行是否背离计划 | 诊断 |

每个 Judge Score 都必须保存 `judge_model`、`rubric_version`、输入 Span 引用、原始分数、归一化分数和脱敏理由。Judge 失败、模型不可用或未提供计划时返回 `not_available`，不污染确定性 Gate。

## 4. 数据契约增量

保留既有 `agent.run`、`model.call` 和 `tool.call`；SDK 接入阶段增加以下**可选且脱敏**字段：

```json
{
  "name": "tool.call",
  "attributes": {
    "tool_name": "read_file",
    "target_path": "src/calculator.py",
    "argument_keys": ["path"],
    "argument_fingerprint": "sha256:..."
  }
}
```

随后增加 `test.run` Span，平台测试执行也应输出统一的 `test_command_id`、`exit_code`、`duration_ms` 和已截断的失败摘要。`target_path` 必须是 Worktree 相对路径，不能接受绝对路径或 `..` 路径逃逸。

## 5. Experiment Report v2

`experiment.json` 在保持既有 `comparison` 字段兼容的基础上，新增：

```json
{
  "metrics_version": 3,
  "trial_count_required_per_case": 3,
  "pricing_version": "v1",
  "case_comparisons": [],
  "reliability": {},
  "efficiency": {},
  "behavior": {},
  "statistics": {}
}
```

`case_comparisons` 必须保留同一 `case_id`、`trial_index` 下 Baseline/Candidate 的配对结果，供 Console 并列查看。报告同时要列出不可计算指标及原因。

## 6. 实施顺序

1. 在 `experiment.py` 增加 Case group 聚合：all-pass@3、Flaky Case Rate、逐 Case 配对差异，以及 P50/P95；为固定输入编写单元测试。
2. ✅ Gate 加入 all-pass@3、Flaky Case Rate、Path/Diff 违规率；平均耗时与 P95 尾延迟保留为诊断，正确性/可靠性回归优先于 Token 节省。
3. 扩展 Dashboard API 和 Console，展示 Reliability、Efficiency、Behavior 三个诊断面板。
4. ✅ Observer SDK 与内置/外部参考 Agent 已写入脱敏 `target_path`、`argument_keys`、`argument_fingerprint`，并实现确定性 Tool 指标。历史 Trace 保持兼容：能重算的 Tool Outcome 会回填，缺失语义字段的指标显式为 `not_available`。
5. ✅ 对外部 Agent 的配对耗时、Token、工具调用计算固定种子的按 Case 聚类 Bootstrap 95% 区间、Case 胜负分布和证据等级；仅作诊断，不直接阻断 Gate。
6. 在 8 Case × 3 Trial × 2 Version 真实实验后，再配置版本化模型价格表并生成成本报告。
7. 最后单独实现 LLM Judge Provider；先离线运行、人工审查样本，再讨论是否让其影响 Gate。

## 7. 非目标

- 不在 v2.0 声称统计显著性或以 p-value 决定发布；
- 不将思维链、完整 Prompt、完整文件内容写入 Trace；
- 不通过自动埋点猜测未被 SDK 包裹的外部工具调用；
- 不因增加指标而引入远程数据平台、账号体系或公开写接口。
