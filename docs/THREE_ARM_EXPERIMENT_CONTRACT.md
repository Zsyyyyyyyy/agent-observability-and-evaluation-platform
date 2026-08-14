# Three-arm formal experiment contract

The next formal external-Agent experiment uses one shared Champion and two
independent candidate arms. This is a falsifiable release decision, not a
three-way leaderboard.

| Arm | Version | Expected outcome |
| --- | --- | --- |
| Champion | `external-openai-v3` | Reference evidence |
| Positive candidate | `external-openai-v4.1` | Preserve validity while reducing post-verification work |
| Negative control | `external-openai-v3-negative` | V3 Prompt and normal loop, followed by two redundant model completions |

Every Case/Trial pair is scheduled once for each arm in a shuffled local order.
For eight Cases and three Trials, the protocol has 72 real Trials. The report
stores `comparison_arms` so both candidate arms are compared independently
against the same Champion. Its legacy top-level `comparison` remains the
positive arm for dashboard compatibility.

`external-openai-v3-negative` is intentionally not a product candidate. Its
Prompt is byte-for-byte V3 and it runs the ordinary V3 loop to its natural
terminal model reply. Only then the platform records two
`negative_control_redundant_call` events, requests two model completions, and
stops without executing their tools. This makes extra cost the sole controlled
intervention relative to V3. A Gate that promotes it is evidence that the
efficiency policy is too weak.

The three commands are intentionally separate: the first is the only command
that calls the model; the second materializes deterministic Gate reports; the
third is read-only acceptance.

Before a real run, freeze the protocol and execute it with:

```sh
make external-three-arm-benchmark
make external-three-arm-gates
make audit-three-arm-benchmark
```

Acceptance is deliberately asymmetric: the positive arm must be `PROMOTE`,
the negative arm must be `HOLD`, all 72 Trials must have terminal artifacts,
and both Gate reports must cite strict protocol comparability and complete
eight-Case coverage. Any other result is diagnostic evidence, not a release.

## Corrected negative-control validation

The first three-arm run showed that a V4.1-derived negative control remained
more efficient than V3, so its `PROMOTE` was correct and did not challenge the
Gate. The corrected validation is a separate, fresh two-arm experiment:

```sh
make external-v3-negative-control
make external-v3-negative-gate
make audit-v3-negative-control
```

It runs 48 real Trials (V3 and V3-negative, eight Cases, three repeats). It
does not overwrite the completed V3/V4.1 evidence. The expected decision for
the negative arm is `HOLD`.

### V2 formal evidence

The current formal result is deliberately stored in a fresh artifact root:

```sh
make external-v3-negative-control-v2
make external-v3-negative-gate-v2 # exits 1 when the expected HOLD is produced
make audit-v3-negative-control-v2
```

It adds an independent runtime source-hash check: every selected external
Agent Attempt must match the entry-point hash frozen in `protocol.json`.
Missing or mismatched runtime identity makes the Experiment non-comparable.
The completed V2 result is documented in
[`EXPERIMENT_REPORT_EXTERNAL_V3_NEGATIVE_CONTROL_V2.md`](./EXPERIMENT_REPORT_EXTERNAL_V3_NEGATIVE_CONTROL_V2.md).
