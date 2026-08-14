PYTHON ?= python3.11
PYTHONPATH_VALUE := src:.
RUNTIME ?= .runtime/core-experiment-v1
REAL_OUTPUT_DIR ?= .runtime/react-smoke

.DEFAULT_GOAL := help
.PHONY: help test docker-test manifest-check replay-dry-run real-smoke external-real-smoke external-experiment external-evolution external-statistical-evolution external-v4-preexperiment external-v4-1-preexperiment external-v4-1-benchmark audit-v4-1-benchmark external-v4-1-benchmark-v2 external-v4-1-benchmark-v2-gate audit-v4-1-benchmark-v2 external-three-arm-benchmark external-three-arm-gates audit-three-arm-benchmark external-v3-negative-control external-v3-negative-gate audit-v3-negative-control external-v3-negative-control-v2 external-v3-negative-gate-v2 audit-v3-negative-control-v2 external-v3-negative-control-benchmark-v2 external-v3-negative-control-benchmark-v2-gate audit-v3-negative-control-benchmark-v2 apply-external-lineage console experiment-report gate failure-suite

help:
	@printf '%s\n' 'Targets:' \
	  '  make test              Run deterministic unit tests (Docker tests skipped).' \
	  '  make docker-test       Run Docker sandbox integration tests.' \
	  '  make manifest-check    Validate and expand the core benchmark manifests.' \
	  '  make replay-dry-run    Validate the replay benchmark execution plan.' \
	  '  make experiment-report Rebuild an existing experiment report without model calls.' \
	  '  make gate              Evaluate the default promotion policy against a report.' \
	  '  make failure-suite     Run deterministic expected-failure probes in Docker.' \
	  '  make real-smoke        Run one real react-agent trial (requires model env).' \
	  '  make external-real-smoke Run one real framework-neutral Agent trial (requires model env).' \
	  '  make external-experiment Run 3-case v1/v2 external-Agent experiment (requires model env).' \
	  '  make external-evolution Run the next 3-case v2/v3 external-Agent experiment (requires model env).' \
	  '  make external-statistical-evolution Run 8-case v2/v3 repeated experiment for statistical evidence.' \
	  '  make external-v4-preexperiment Run the isolated 3-case v3/v4 pre-experiment (requires model env).' \
	  '  make external-v4-1-preexperiment Run the isolated 3-case v3/v4.1 pre-experiment (requires model env).' \
	  '  make external-v4-1-benchmark Run the 8-case v3/v4.1 confirmation benchmark (requires model env).' \
	  '  make external-v4-1-benchmark-v2 Run the 11-case V3/V4.1 confirmation benchmark (requires model env).' \
	  '  make external-v3-negative-control-benchmark-v2 Run the 11-case negative control (requires model env).' \
	  '  make audit-v4-1-benchmark Verify the existing V4.1 benchmark artifacts offline.' \
	  '  make apply-external-lineage Apply the reviewed external-Agent version lineage locally.' \
	  '  make console           Serve the read-only local observability console.'

test:
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) -m unittest discover -s tests -q

docker-test:
	RUN_DOCKER_INTEGRATION=1 PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) -m unittest tests.test_sandbox_integration -v

manifest-check:
	@for manifest in benchmarks/*-case*.yaml; do \
	  PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) scripts/run_benchmark.py --manifest $$manifest --dry-run || exit $$?; \
	done

replay-dry-run:
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) scripts/run_benchmark.py \
	  --adapter readonly-replay --manifest benchmarks/smoke-case-design.yaml --dry-run

real-smoke:
	@test -n "$(AGENT_API_KEY)" || (echo 'AGENT_API_KEY is required.' >&2; exit 2)
	@test -n "$(AGENT_MODEL)" || (echo 'AGENT_MODEL is required.' >&2; exit 2)
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) scripts/run_benchmark.py \
	  --adapter react-agent --agent-version react-agent-v1 \
	  --manifest benchmarks/smoke-case-design.yaml --output-dir $(REAL_OUTPUT_DIR) --resume

external-real-smoke:
	@test -n "$(AGENT_API_KEY)" || (echo 'AGENT_API_KEY is required.' >&2; exit 2)
	@test -n "$(AGENT_MODEL)" || (echo 'AGENT_MODEL is required.' >&2; exit 2)
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) scripts/run_benchmark.py \
	  --adapter external-command --agent-version external-openai-v1 \
	  --external-command '["$(PYTHON)", "$(CURDIR)/examples/external_openai_agent.py"]' \
	  --manifest benchmarks/smoke-case-design.yaml --output-dir .runtime/external-openai-smoke --resume

external-experiment:
	@test -n "$(AGENT_API_KEY)" || (echo 'AGENT_API_KEY is required.' >&2; exit 2)
	@test -n "$(AGENT_MODEL)" || (echo 'AGENT_MODEL is required.' >&2; exit 2)
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) scripts/run_experiment.py \
	  --adapter external-command \
	  --external-command '["$(PYTHON)", "$(CURDIR)/examples/external_openai_agent.py"]' \
	  --agents baseline:external-openai-v1,candidate:external-openai-v2 \
	  --trials 3 --output-dir .runtime/external-openai-v1-v2 \
	  --manifest benchmarks/smoke-case-design.yaml \
	  --manifest benchmarks/normalize-case-design.yaml \
	  --manifest benchmarks/parse-port-case.yaml

external-evolution:
	@test -n "$(AGENT_API_KEY)" || (echo 'AGENT_API_KEY is required.' >&2; exit 2)
	@test -n "$(AGENT_MODEL)" || (echo 'AGENT_MODEL is required.' >&2; exit 2)
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) scripts/run_experiment.py \
	  --adapter external-command \
	  --external-command '["$(PYTHON)", "$(CURDIR)/examples/external_openai_agent.py"]' \
	  --agents baseline:external-openai-v2,candidate:external-openai-v3 \
	  --trials 3 --output-dir .runtime/external-openai-v2-v3 \
	  --evolution-catalog .runtime/evolution-catalog.json \
	  --manifest benchmarks/smoke-case-design.yaml \
	  --manifest benchmarks/normalize-case-design.yaml \
	  --manifest benchmarks/parse-port-case.yaml

external-statistical-evolution:
	@test -n "$(AGENT_API_KEY)" || (echo 'AGENT_API_KEY is required.' >&2; exit 2)
	@test -n "$(AGENT_MODEL)" || (echo 'AGENT_MODEL is required.' >&2; exit 2)
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) scripts/run_experiment.py \
	  --adapter external-command \
	  --external-command '["$(PYTHON)", "$(CURDIR)/examples/external_openai_agent.py"]' \
	  --agents baseline:external-openai-v2,candidate:external-openai-v3 \
	  --trials 3 --resume --output-dir .runtime/external-openai-v2-v3-statistical \
	  --evolution-catalog .runtime/evolution-catalog.json \
	  --manifest benchmarks/smoke-case-design.yaml \
	  --manifest benchmarks/normalize-case-design.yaml \
	  --manifest benchmarks/cross-file-greeting-case.yaml \
	  --manifest benchmarks/safe-slug-case.yaml \
	  --manifest benchmarks/bounded-discount-case.yaml \
	  --manifest benchmarks/deduplicate-tags-case.yaml \
	  --manifest benchmarks/merge-settings-case.yaml \
	  --manifest benchmarks/parse-port-case.yaml

# Stage 9: this target intentionally creates a new artifact root.  Do not add
# --resume: it must freeze a fresh Protocol v2 for the v3/v4 comparison.
external-v4-preexperiment:
	@test -n "$(AGENT_API_KEY)" || (echo 'AGENT_API_KEY is required.' >&2; exit 2)
	@test -n "$(AGENT_MODEL)" || (echo 'AGENT_MODEL is required.' >&2; exit 2)
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) scripts/run_experiment.py \
	  --adapter external-command \
	  --external-command '["$(PYTHON)", "$(CURDIR)/examples/external_openai_agent.py"]' \
	  --agents baseline:external-openai-v3,candidate:external-openai-v4 \
	  --trials 3 --schedule-seed 20260813 \
	  --output-dir .runtime/external-openai-v3-v4-preexperiment \
	  --evolution-catalog .runtime/evolution-catalog.json \
	  --manifest benchmarks/dependency-cycle-case.yaml \
	  --manifest benchmarks/batch-isolation-case.yaml \
	  --manifest benchmarks/profile-migration-case.yaml

# V4.1 has a new runtime success-stop policy, so it always produces a fresh
# Protocol and artifacts. Do not reuse the V3/V4 pre-experiment directory.
external-v4-1-preexperiment:
	@test -n "$(AGENT_API_KEY)" || (echo "AGENT_API_KEY is required" && exit 2)
	@test -n "$(AGENT_MODEL)" || (echo "AGENT_MODEL is required" && exit 2)
	PYTHONPATH=src:. $(PYTHON) scripts/run_experiment.py \
	  --adapter external-command \
	  --external-command '["$(PYTHON)", "$(CURDIR)/examples/external_openai_agent.py"]' \
	  --agents baseline:external-openai-v3,candidate:external-openai-v4.1 \
	  --trials 3 --schedule-seed 20260813 \
	  --output-dir .runtime/external-openai-v3-v4-1-preexperiment \
	  --evolution-catalog .runtime/evolution-catalog.json \
	  --manifest benchmarks/dependency-cycle-case.yaml \
	  --manifest benchmarks/batch-isolation-case.yaml \
	  --manifest benchmarks/profile-migration-case.yaml

# This confirmation benchmark deliberately uses eight Cases and a fresh
# artifact root. It is not a resume of the three-Case V4.1 pre-experiment.
external-v4-1-benchmark:
	@test -n "$(AGENT_API_KEY)" || (echo "AGENT_API_KEY is required" && exit 2)
	@test -n "$(AGENT_MODEL)" || (echo "AGENT_MODEL is required" && exit 2)
	PYTHONPATH=src:. $(PYTHON) scripts/run_experiment.py \
	  --adapter external-command \
	  --external-command '["$(PYTHON)", "$(CURDIR)/examples/external_openai_agent.py"]' \
	  --agents baseline:external-openai-v3,candidate:external-openai-v4.1 \
	  --trials 3 --schedule-seed 20260814 \
	  --output-dir .runtime/external-openai-v3-v4-1-benchmark \
	  --evolution-catalog .runtime/evolution-catalog.json \
	  --manifest benchmarks/dependency-cycle-case.yaml \
	  --manifest benchmarks/batch-isolation-case.yaml \
	  --manifest benchmarks/profile-migration-case.yaml \
	  --manifest benchmarks/cache-expiry-case.yaml \
	  --manifest benchmarks/config-inheritance-case.yaml \
	  --manifest benchmarks/permission-precedence-case.yaml \
	  --manifest benchmarks/safe-slug-case.yaml \
	  --manifest benchmarks/parse-port-case.yaml

# Formal positive/negative-control validation. This performs 72 real model
# Trials; run only after explicitly approving its API-cost and time budget.
external-three-arm-benchmark:
	@test -n "$(AGENT_API_KEY)" || (echo "AGENT_API_KEY is required" && exit 2)
	@test -n "$(AGENT_MODEL)" || (echo "AGENT_MODEL is required" && exit 2)
	PYTHONPATH=src:. $(PYTHON) scripts/run_experiment.py \
	  --adapter external-command \
	  --external-command '["$(PYTHON)", "$(CURDIR)/examples/external_openai_agent.py"]' \
	  --agents champion:external-openai-v3,positive:external-openai-v4.1,negative:external-openai-v4-negative \
	  --trials 3 --schedule-seed 20260815 \
	  --comparison-intent runtime_success_stop_positive_and_negative_control \
	  --allowed-difference agents[].prompt_profile \
	  --allowed-difference agents[].runtime_success_stop_policy \
	  --output-dir .runtime/external-openai-v3-v4-1-negative-benchmark \
	  --evolution-catalog .runtime/evolution-catalog.json \
	  --manifest benchmarks/dependency-cycle-case.yaml \
	  --manifest benchmarks/batch-isolation-case.yaml \
	  --manifest benchmarks/profile-migration-case.yaml \
	  --manifest benchmarks/cache-expiry-case.yaml \
	  --manifest benchmarks/config-inheritance-case.yaml \
	  --manifest benchmarks/permission-precedence-case.yaml \
	  --manifest benchmarks/safe-slug-case.yaml \
	  --manifest benchmarks/parse-port-case.yaml

# Evaluates the two already-persisted arms independently; it never runs a model.
external-three-arm-gates:
	PYTHONPATH=src:. $(PYTHON) scripts/evaluate_gate.py \
	  --experiment .runtime/external-openai-v3-v4-1-negative-benchmark/experiment.json \
	  --comparison-id positive --policy configs/default-gate.json \
	  --output .runtime/external-openai-v3-v4-1-negative-benchmark/gate-positive.json
	PYTHONPATH=src:. $(PYTHON) scripts/evaluate_gate.py \
	  --experiment .runtime/external-openai-v3-v4-1-negative-benchmark/experiment.json \
	  --comparison-id negative --policy configs/default-gate.json \
	  --output .runtime/external-openai-v3-v4-1-negative-benchmark/gate-negative.json

# Read-only final acceptance after the real experiment and its two Gate reports.
audit-three-arm-benchmark:
	PYTHONPATH=src:. $(PYTHON) scripts/audit_three_arm_benchmark.py \
	  --runtime .runtime/external-openai-v3-v4-1-negative-benchmark

# Fresh two-arm Gate challenge: V3's exact normal loop plus two platform
# injected post-terminal model calls. This does not overwrite prior artifacts.
external-v3-negative-control:
	@test -n "$(AGENT_API_KEY)" || (echo "AGENT_API_KEY is required" && exit 2)
	@test -n "$(AGENT_MODEL)" || (echo "AGENT_MODEL is required" && exit 2)
	PYTHONPATH=src:. $(PYTHON) scripts/run_experiment.py \
	  --adapter external-command \
	  --external-command '["$(PYTHON)", "$(CURDIR)/examples/external_openai_agent.py"]' \
	  --agents champion:external-openai-v3,negative:external-openai-v3-negative \
	  --trials 3 --schedule-seed 20260816 \
	  --comparison-intent v3_runtime_redundant_completion_negative_control \
	  --allowed-difference 'agents[].runtime_negative_control_post_terminal_completions' \
	  --output-dir .runtime/external-openai-v3-negative-control \
	  --evolution-catalog .runtime/evolution-catalog.json \
	  --manifest benchmarks/dependency-cycle-case.yaml \
	  --manifest benchmarks/batch-isolation-case.yaml \
	  --manifest benchmarks/profile-migration-case.yaml \
	  --manifest benchmarks/cache-expiry-case.yaml \
	  --manifest benchmarks/config-inheritance-case.yaml \
	  --manifest benchmarks/permission-precedence-case.yaml \
	  --manifest benchmarks/safe-slug-case.yaml \
	  --manifest benchmarks/parse-port-case.yaml

external-v3-negative-gate:
	PYTHONPATH=src:. $(PYTHON) scripts/evaluate_gate.py \
	  --experiment .runtime/external-openai-v3-negative-control/experiment.json \
	  --comparison-id negative --policy configs/default-gate.json \
	  --output .runtime/external-openai-v3-negative-control/gate-negative.json

audit-v3-negative-control:
	PYTHONPATH=src:. $(PYTHON) scripts/audit_v3_negative_control.py \
	  --runtime .runtime/external-openai-v3-negative-control

# Formal re-run after runtime source-identity proof was added.  Keep this
# separate from the historical control so its conclusion has one clean scope.
external-v3-negative-control-v2:
	@test -n "$(AGENT_API_KEY)" || (echo "AGENT_API_KEY is required" && exit 2)
	@test -n "$(AGENT_MODEL)" || (echo "AGENT_MODEL is required" && exit 2)
	PYTHONPATH=src:. $(PYTHON) scripts/run_experiment.py \
	  --adapter external-command \
	  --external-command '["$(PYTHON)", "$(CURDIR)/examples/external_openai_agent.py"]' \
	  --agents champion:external-openai-v3,negative:external-openai-v3-negative \
	  --trials 3 --schedule-seed 20260818 \
	  --comparison-intent v3_runtime_redundant_completion_negative_control \
	  --allowed-difference 'agents[].runtime_negative_control_post_terminal_completions' \
	  --resume \
	  --output-dir .runtime/external-openai-v3-negative-control-v2 \
	  --evolution-catalog .runtime/evolution-catalog.json \
	  --manifest benchmarks/dependency-cycle-case.yaml \
	  --manifest benchmarks/batch-isolation-case.yaml \
	  --manifest benchmarks/profile-migration-case.yaml \
	  --manifest benchmarks/cache-expiry-case.yaml \
	  --manifest benchmarks/config-inheritance-case.yaml \
	  --manifest benchmarks/permission-precedence-case.yaml \
	  --manifest benchmarks/safe-slug-case.yaml \
	  --manifest benchmarks/parse-port-case.yaml

external-v3-negative-gate-v2:
	PYTHONPATH=src:. $(PYTHON) scripts/evaluate_gate.py \
	  --experiment .runtime/external-openai-v3-negative-control-v2/experiment.json \
	  --comparison-id negative --policy configs/default-gate.json \
	  --output .runtime/external-openai-v3-negative-control-v2/gate-negative.json

audit-v3-negative-control-v2:
	PYTHONPATH=src:. $(PYTHON) scripts/audit_v3_negative_control.py \
	  --runtime .runtime/external-openai-v3-negative-control-v2

# Expanded Benchmark v2. Each target owns a new root and can only be run after
# explicit approval for its 66 real model Trials (11 Cases x 3 repeats x 2 arms).
external-v4-1-benchmark-v2:
	@test -n "$(AGENT_API_KEY)" || (echo "AGENT_API_KEY is required" && exit 2)
	@test -n "$(AGENT_MODEL)" || (echo "AGENT_MODEL is required" && exit 2)
	PYTHONPATH=src:. $(PYTHON) scripts/run_experiment.py \
	  --adapter external-command \
	  --external-command '["$(PYTHON)", "$(CURDIR)/examples/external_openai_agent.py"]' \
	  --agents baseline:external-openai-v3,candidate:external-openai-v4.1 \
	  --trials 3 --schedule-seed 20260820 --resume \
	  --output-dir .runtime/external-openai-v3-v4-1-benchmark-v2 \
	  --evolution-catalog .runtime/evolution-catalog.json \
	  --manifest benchmarks/dependency-cycle-case.yaml \
	  --manifest benchmarks/batch-isolation-case.yaml \
	  --manifest benchmarks/profile-migration-case.yaml \
	  --manifest benchmarks/cache-expiry-case.yaml \
	  --manifest benchmarks/config-inheritance-case.yaml \
	  --manifest benchmarks/permission-precedence-case.yaml \
	  --manifest benchmarks/safe-slug-case.yaml \
	  --manifest benchmarks/parse-port-case.yaml \
	  --manifest benchmarks/inventory-reservation-case.yaml \
	  --manifest benchmarks/cursor-revision-case.yaml \
	  --manifest benchmarks/webhook-signature-case.yaml

external-v4-1-benchmark-v2-gate:
	PYTHONPATH=src:. $(PYTHON) scripts/evaluate_gate.py \
	  --experiment .runtime/external-openai-v3-v4-1-benchmark-v2/experiment.json \
	  --policy configs/default-gate.json \
	  --output .runtime/external-openai-v3-v4-1-benchmark-v2/gate-report.json

audit-v4-1-benchmark-v2:
	PYTHONPATH=src:. $(PYTHON) scripts/audit_v4_1_benchmark.py \
	  --runtime .runtime/external-openai-v3-v4-1-benchmark-v2 --expected-case-count 11

external-v3-negative-control-benchmark-v2:
	@test -n "$(AGENT_API_KEY)" || (echo "AGENT_API_KEY is required" && exit 2)
	@test -n "$(AGENT_MODEL)" || (echo "AGENT_MODEL is required" && exit 2)
	PYTHONPATH=src:. $(PYTHON) scripts/run_experiment.py \
	  --adapter external-command \
	  --external-command '["$(PYTHON)", "$(CURDIR)/examples/external_openai_agent.py"]' \
	  --agents champion:external-openai-v3,negative:external-openai-v3-negative \
	  --trials 3 --schedule-seed 20260821 --resume \
	  --comparison-intent v3_runtime_redundant_completion_negative_control \
	  --allowed-difference 'agents[].runtime_negative_control_post_terminal_completions' \
	  --output-dir .runtime/external-openai-v3-negative-control-benchmark-v2 \
	  --evolution-catalog .runtime/evolution-catalog.json \
	  --manifest benchmarks/dependency-cycle-case.yaml \
	  --manifest benchmarks/batch-isolation-case.yaml \
	  --manifest benchmarks/profile-migration-case.yaml \
	  --manifest benchmarks/cache-expiry-case.yaml \
	  --manifest benchmarks/config-inheritance-case.yaml \
	  --manifest benchmarks/permission-precedence-case.yaml \
	  --manifest benchmarks/safe-slug-case.yaml \
	  --manifest benchmarks/parse-port-case.yaml \
	  --manifest benchmarks/inventory-reservation-case.yaml \
	  --manifest benchmarks/cursor-revision-case.yaml \
	  --manifest benchmarks/webhook-signature-case.yaml

external-v3-negative-control-benchmark-v2-gate:
	PYTHONPATH=src:. $(PYTHON) scripts/evaluate_gate.py \
	  --experiment .runtime/external-openai-v3-negative-control-benchmark-v2/experiment.json \
	  --comparison-id negative --policy configs/default-gate.json \
	  --output .runtime/external-openai-v3-negative-control-benchmark-v2/gate-negative.json

audit-v3-negative-control-benchmark-v2:
	PYTHONPATH=src:. $(PYTHON) scripts/audit_v3_negative_control.py \
	  --runtime .runtime/external-openai-v3-negative-control-benchmark-v2 --expected-case-count 11

audit-v4-1-benchmark:
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) scripts/audit_v4_1_benchmark.py \
	  --runtime .runtime/external-openai-v3-v4-1-benchmark

apply-external-lineage:
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) scripts/apply_evolution_lineage.py \
	  --catalog .runtime/evolution-catalog.json \
	  --declaration configs/external-openai-lineage.json

experiment-report:
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) scripts/run_experiment.py \
	  --manifest benchmarks/smoke-case-design.yaml \
	  --manifest benchmarks/normalize-case-design.yaml \
	  --manifest benchmarks/cross-file-greeting-case.yaml \
	  --manifest benchmarks/safe-slug-case.yaml \
	  --manifest benchmarks/bounded-discount-case.yaml \
	  --manifest benchmarks/deduplicate-tags-case.yaml \
	  --manifest benchmarks/merge-settings-case.yaml \
	  --manifest benchmarks/parse-port-case.yaml \
	  --output-dir $(RUNTIME) --adapter react-agent \
	  --agents baseline:react-agent-v1,candidate:react-agent-v2 --trials 3 --report-only

gate:
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) scripts/evaluate_gate.py \
	  --experiment $(RUNTIME)/experiment.json --policy configs/default-gate.json \
	  --output $(RUNTIME)/gate-report.json

failure-suite:
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) scripts/run_failure_suite.py

console:
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) scripts/serve_dashboard.py --runtime $(RUNTIME)
