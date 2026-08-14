# Agent Evolution Schema v1

阶段 1 定义 Agent 版本记忆与实验历史的数据契约。它不是对话记忆，也不把 Trace 内容塞进向量数据库；它是一个面向发布治理的、可审计的版本谱系索引。

## 1. 设计目标

- 保留现有 JSONL Trace、Result、Git Diff 和 SQLite Run Store 作为不可变运行证据。
- 用轻量 Catalog 索引 Agent 版本、实验、Trial、Attempt 和 Gate 决策。
- 允许从 `v1 → v2 → v3` 查看 Agent 的演进，而不把历史结果覆盖成“当前 v1/v2”。
- 把 `Trial` 与 `Attempt` 分开：一次 Trial 可以因为超时或模型失败产生多次 Attempt，但最终统计最多选择一个有效 Attempt。
- 在纵向比较前验证实验上下文是否兼容，避免把不同 Case、模型、Evaluator 或预算下的数字直接连成趋势。

## 2. 实体关系

```text
Agent
 └── AgentVersion (parent_version_id)
      └── Experiment (baseline/candidate)
           └── Case
                └── Trial (trial_index)
                     └── Attempt (attempt_index, selected)
            └── GateDecision (immutable release decision)
```

`Experiment` 的 `baseline_version_id` 和 `candidate_version_id` 可以引用同一个 Agent 的两个版本；`Case` 引用已有 Benchmark Manifest 的不可变指纹。原始 Artifact 通过 `artifact_root`、`artifact_dir` 和 `trace_id` 定位，不在 Catalog 中重复保存大文件。

## 3. 核心字段

### AgentVersion

| 字段 | 含义 |
| --- | --- |
| `version_id` | Catalog 内安全唯一 ID |
| `agent_id` | 外部 Agent 的稳定身份 |
| `version` | 对外展示的版本标签 |
| `parent_version_id` | 父版本；首个版本为 `null` |
| `status` | `draft/candidate/champion/rejected/archived` |
| `change_type` | `code/prompt/model/tools/config/mixed` |
| `change_summary` | 面向人的改动摘要 |
| `snapshot` | adapter、model、Prompt Profile、工具和配置指纹；禁止保存密钥 |

### Experiment

一次不可变的比较运行。它保存 baseline/candidate、Case 集、Evaluator/Gate 版本和 `evaluation_context_hash`。实验完成后不应原地改写；重新执行应创建新的 Experiment 或新的 Attempt。

### Trial / Attempt

`Trial` 是统计单位，例如某 Case 的第 2 次重复；`Attempt` 是实际进程执行。只有 `selected_attempt_id` 指向的 Attempt 进入 Experiment 报告。无效 Attempt 必须保留用于审计，但不能进入 Pass@3、延迟或 Token 汇总。

`GateDecision` 保存一次实验的不可变晋级结论：`promote`、`hold` 或 `inconclusive`。它引用 Gate Policy 版本、规则结果和 Experiment 证据；后续重跑不能修改旧决策，只能产生新的 Experiment/GateDecision。

## 4. 可比性指纹

`evaluation_context` 至少包含：

```json
{
  "case_ids": ["smoke_calculator_empty_input"],
  "manifest_hashes": {"smoke_calculator_empty_input": "sha256:..."},
  "fixture_hashes": {"smoke_calculator_empty_input": "sha256:..."},
  "test_hashes": {"smoke_calculator_empty_input": "sha256:..."},
  "agent_snapshot": "sha256:...",
  "evaluator_version": "evaluators-v2",
  "gate_policy_version": "gate-v2",
  "sandbox_image": "python:3.11-slim",
  "budget": {"max_tool_calls": 24, "max_duration_ms": 180000}
}
```

使用 `sha256:` + 稳定 JSON 的 SHA-256 作为 `evaluation_context_hash`。上下文相同才允许严格纵向比较；Case 子集变化时标记 `partially_comparable`；测试、Evaluator 或执行环境变化时标记 `not_comparable`，但历史数据仍可查看。

## 5. 状态与不变量

- 一个 `version_id` 只能对应一个 Agent；父版本必须存在且不能形成环。
- 同一 Agent 在 Catalog 中最多有一个当前 `champion` 版本；历史 Champion 通过 Version Event 或状态变更保留审计。
- 一个 `trial_id` 只能属于一个 Experiment、Case 和 Agent Version。
- `attempt_id` 全局唯一；Attempt 的 Artifact 目录不能复用。
- Terminal Attempt 必须有 `ended_at`；运行中 Attempt 不得被选为统计 Attempt。
- `completed` Trial 必须有 `selected_attempt_id`，且该 ID 必须属于自己的 `attempt_ids`。
- Gate 只引用 Experiment 的平台 Score，不接受 Agent 自报的评分或结论。
- Catalog 可以索引旧 Artifact，但不能修改旧 Experiment 的决策和上下文指纹。

## 6. 实现入口

Schema 校验与上下文指纹位于 [`src/regression_lab/evolution.py`](../src/regression_lab/evolution.py)，持久化 Catalog 位于 [`src/regression_lab/evolution_catalog.py`](../src/regression_lab/evolution_catalog.py)。Catalog 是经过校验的 JSON 索引，不复制 Trace/Result/Git Diff，也不改写它们。

每个 `Experiment` 额外记录 `comparison_summary`（Baseline、Candidate 与四项 delta：评测通过率、平均时延、平均 Token、平均工具调用）和 `comparison_basis`。后者只指纹化 Case、fixture、test、tool policy、evaluator 与重复次数，刻意排除 Agent 版本；Timeline 因而能把连续实验标成 strict、partial 或 none，避免不同基准的数值被包装成优化趋势。

`scripts/run_experiment.py` 会在生成 `experiment.json` 后默认写入 `<output-dir>/../evolution-catalog.json`；`scripts/evaluate_gate.py` 会把 Gate Decision 追加到同一 Catalog。可以用以下命令只读查询某个 Agent 的演进链：

```bash
PYTHONPATH=src:. python3.11 scripts/query_evolution.py \
  --catalog .runtime/evolution-catalog.json \
  --agent-id external-openai
```

Gate 的 `promote` 仅表示本次 Gate 建议晋级，不自动把 Candidate 的版本状态改成 `champion`。版本发布仍需由维护者显式确认，避免把一次有限样本的实验结论误记为正式版本状态。
