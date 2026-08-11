# Core Suite v1：react-agent-v1 vs react-agent-v2

## Setup

- Cases: 8 deterministic Python repair tasks
- Trials: 1 per Case and Agent Version
- Shared controls: same model, Docker Sandbox, tool allowlist, path policy, test command and Evaluators
- Difference: v2 adds the `verify-once-v2` control profile only

## Result

| Metric | v1 Baseline | v2 Candidate | Delta |
|---|---:|---:|---:|
| completion rate | 100% | 100% | 0 pp |
| evaluation pass rate | 100% | 100% | 0 pp |
| test pass rate | 100% | 100% | 0 pp |
| average tool calls | 5.38 | 6.00 | +0.63 |
| average duration | 15.94 s | 12.92 s | -3.02 s (-19.0%) |
| average model tokens | 6,135.5 | 6,321.0 | +185.5 (+3.0%) |

## Interpretation

v2 preserved correctness and reduced wall-clock duration, but it did not reduce tool or token consumption. With one Trial per Case, model sampling and provider latency can affect these values; therefore the correct conclusion is **a latency/tool-cost trade-off**, not a demonstrated overall improvement.

The next statistically stronger experiment should use at least three Trials per Case, report median and variance, and only promote v2 if it keeps pass rate while improving a predefined efficiency target.

## Evidence

Raw report: `.runtime/core-experiment-v1/experiment.json`. Individual Trial Result, Trace and Git Diff files remain under the corresponding `baseline/` and `candidate/` Case directories.
