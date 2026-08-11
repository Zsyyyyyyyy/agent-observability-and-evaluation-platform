# react-agent v1 vs v2 Experiment

`react-agent-v1` is the initial ReAct policy. Its first real Core Suite runs passed every collected Case, but expensive traces exposed repeated discovery and verification calls.

`react-agent-v2` keeps the same model, tools, Sandbox, limits and Evaluators. The only intervention is the versioned `verify-once-v2` operating policy: inspect relevant code/tests, make one minimal edit, run the exact test command once, and recover only after a reported failure. This makes the comparison attributable to Agent control policy rather than a weaker safety boundary.

Run a paired experiment in a terminal with the model environment variables configured:

```bash
cd <repository-root>

python3.11 scripts/run_experiment.py \
  --adapter react-agent \
  --agents baseline:react-agent-v1,candidate:react-agent-v2 \
  --output-dir .runtime/core-experiment-v1 \
  --manifest benchmarks/smoke-case-design.yaml \
  --manifest benchmarks/normalize-case-design.yaml \
  --manifest benchmarks/parse-port-case.yaml \
  --manifest benchmarks/safe-slug-case.yaml \
  --manifest benchmarks/bounded-discount-case.yaml \
  --manifest benchmarks/cross-file-greeting-case.yaml \
  --manifest benchmarks/merge-settings-case.yaml \
  --manifest benchmarks/deduplicate-tags-case.yaml
```

The generated `experiment.json` compares completion, evaluation and test pass rates, plus average tool calls, duration and Diff size. The v2 Profile is written to each Trial Result and root Trace Span.

Efficiency metrics are directional: fewer tool calls, less duration and fewer model tokens are improvements. A candidate with unchanged correctness but mixed efficiency changes is reported as a trade-off, not as an unqualified upgrade.
