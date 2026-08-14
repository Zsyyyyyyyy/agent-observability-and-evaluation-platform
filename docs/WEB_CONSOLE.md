# Observability Console

Console 是一个只读本地 Web 界面。它不运行 Agent、不读取模型密钥、不写入 Run Store；它只查询已完成 Trial 的 `result.json`、`trace.jsonl`、`experiment.json`、可选的 `gate-report.json` 与 Evolution Catalog。

## Start

```bash
cd <repository-root>
make console RUNTIME=.runtime/core-experiment-v1
```

Open `http://127.0.0.1:8765`.

Console 是按启动时传入的 Runtime 读取 Artifact 的。完成新的实验后，需要重启服务并显式切换 Runtime，例如：

```bash
make console RUNTIME=.runtime/external-openai-v2-v3
```

如果只打开旧服务或旧端口，页面可能仍显示旧实验；如果服务代码没有包含 `/api/evolution`，页面会标记 `PARTIAL API`，但仍会保留已有 Dashboard、Trial 和 Experiment 数据。

## Read-only API

| Endpoint | Purpose |
|---|---|
| `/api/dashboard` | aggregate Trial count, pass rate, duration, tool calls, Tokens |
| `/api/trials` | compact Trial list for the table |
| `/api/trials/<console-id>` | full Result plus parsed Trace events |
| `/api/experiments/latest` | Baseline/Candidate comparison report, if present |
| `/api/gate/latest` | current promotion Gate report, if present |
| `/api/evolution` | Agent-lineage versions, Experiment ledger, comparability labels, and Gate decisions from the local Evolution Catalog |

The `<console-id>` is the Trial directory relative to the selected runtime root. Path traversal is rejected by the repository layer.

## Information architecture

The default screen is a release-decision view:

1. **Promotion Gate**：directly displays the candidate decision and key deltas.
2. **Case Comparison Matrix**：one row per Case, with v1 and v2 side by side. Each side shows pass count, median latency, Token, tool calls and any failure state.
3. **Case Explorer**：select one of the eight Cases, switch between Latency, Tokens and Tool calls, and inspect grouped v1/v2 bars for each Trial. The summary uses successful-Trial medians; failed or missing values are shown as striped/N/A instead of zero.
4. **Paired Inspection**：the selected Case keeps all three Trials for both versions in parallel; selecting a Trial opens a Trace/Diff inspector that resolves `tool.call` spans to real tool names, shows duration/status/output previews, and pairs a readable diff summary with the raw color-coded patch.
5. **Evolution Timeline**：links Baseline → Candidate version lineage with change type, prompt/profile snapshot, evaluation-context fingerprint and the attached Gate decision. Its Experiment Ledger persists Pass rate、Latency、Token、Tool-call deltas, and labels consecutive comparisons as **strictly comparable**, **partially comparable**, or **not comparable** according to the Case/fixture/test/policy/evaluator/repeat-count fingerprint. Clicking a version expands its change summary. `promote` remains a Gate recommendation; it does not automatically change a version to `champion`.
6. **Trial Triage**：the raw 48-row index remains available for filtering and incident investigation.

Archived `invalid-attempts/` are intentionally excluded from dashboard totals and comparison rows; they remain on disk for audit only.

## Current scope

MVP renders local Artifact data only. It deliberately does not provide authentication, multi-user mutation, remote model execution or browser-side access to model credentials.
