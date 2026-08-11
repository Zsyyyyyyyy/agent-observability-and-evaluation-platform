PYTHON ?= python3.11
PYTHONPATH_VALUE := src:.
RUNTIME ?= .runtime/core-experiment-v1
REAL_OUTPUT_DIR ?= .runtime/react-smoke

.DEFAULT_GOAL := help
.PHONY: help test docker-test manifest-check replay-dry-run real-smoke console experiment-report gate failure-suite

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
	  --adapter s20-replay --manifest benchmarks/smoke-case-design.yaml --dry-run

real-smoke:
	@test -n "$(AGENT_API_KEY)" || (echo 'AGENT_API_KEY is required.' >&2; exit 2)
	@test -n "$(AGENT_MODEL)" || (echo 'AGENT_MODEL is required.' >&2; exit 2)
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) scripts/run_benchmark.py \
	  --adapter react-agent --agent-version react-agent-v1 \
	  --manifest benchmarks/smoke-case-design.yaml --output-dir $(REAL_OUTPUT_DIR) --resume

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
