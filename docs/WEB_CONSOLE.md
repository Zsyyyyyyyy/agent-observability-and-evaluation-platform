# Observability Console

Console 是一个只读本地 Web 界面。它不运行 Agent、不读取模型密钥、不写入 Run Store；它只查询已完成 Trial 的 `result.json`、`trace.jsonl`、`experiment.json` 与可选的 `gate-report.json`。

## Start

```bash
cd <repository-root>
make console RUNTIME=.runtime/core-experiment-v1
```

Open `http://127.0.0.1:8765`.

## Read-only API

| Endpoint | Purpose |
|---|---|
| `/api/dashboard` | aggregate Trial count, pass rate, duration, tool calls, Tokens |
| `/api/trials` | compact Trial list for the table |
| `/api/trials/<console-id>` | full Result plus parsed Trace events |
| `/api/experiments/latest` | Baseline/Candidate comparison report, if present |
| `/api/gate/latest` | current promotion Gate report, if present |

The `<console-id>` is the Trial directory relative to the selected runtime root. Path traversal is rejected by the repository layer.

## Information architecture

The default screen is a release-decision view:

1. **Promotion Gate**：directly displays the candidate decision and key deltas.
2. **Case Comparison Matrix**：one row per Case, with v1 and v2 side by side. Each side shows pass count, median latency, Token, tool calls and any failure state.
3. **Case Explorer**：select one of the eight Cases, switch between Latency, Tokens and Tool calls, and inspect grouped v1/v2 bars for each Trial. The summary uses successful-Trial medians; failed or missing values are shown as striped/N/A instead of zero.
4. **Paired Inspection**：the selected Case keeps all three Trials for both versions in parallel; selecting a Trial opens a Trace/Diff inspector that resolves `tool.call` spans to real tool names, shows duration/status/output previews, and pairs a readable diff summary with the raw color-coded patch.
5. **Trial Triage**：the raw 48-row index remains available for filtering and incident investigation.

Archived `invalid-attempts/` are intentionally excluded from dashboard totals and comparison rows; they remain on disk for audit only.

## Current scope

MVP renders local Artifact data only. It deliberately does not provide authentication, multi-user mutation, remote model execution or browser-side access to model credentials.
