# v0.1.0 Release Audit

Audit date: 2026-08-11

## Result

The repository is ready to become an independent GitHub repository. The default runnable path is `react-agent`; `s20-replay` remains an optional external read-only Bridge and requires an explicit `--s20-source` path.

## Checks passed

- `make test`: 52 passed, 3 Docker integration tests intentionally skipped by the fast suite.
- `make docker-test`: 3/3 Docker isolation tests passed.
- `make failure-suite`: 3/3 expected-failure probes passed.
- `make manifest-check`: all 8 core benchmark manifests expanded successfully.
- `make replay-dry-run`: optional S20 Bridge plan validates without bundling external S20 source.
- `make experiment-report RUNTIME=.runtime/repeated-experiment-v1-v2`: rebuilt the full 8 Case × 3 Trial × 2 Version report from persisted Artifacts without a model call.
- `make gate RUNTIME=.runtime/repeated-experiment-v1-v2`: 8/8 promotion rules passed.
- A clean temporary copy excluding `.env` and `.runtime` passed `make test`, `make manifest-check`, and `make replay-dry-run`.

## Safety checks

- `.env`, `.runtime/`, local databases and Python caches are ignored.
- Source and docs were scanned for credential-shaped literals; only variable names and configuration documentation remain.
- The core repository no longer depends on an absolute local path or the parent teaching repository.

## Known scope

- This is a local, single-machine MVP. It does not provide multi-user authentication or remote Artifact storage.
- Running real `react-agent` experiments requires configured model environment variables.
- The external S20 source is intentionally not included; use `--s20-source /path/to/s20_comprehensive/code.py` only when that optional Bridge is needed.
