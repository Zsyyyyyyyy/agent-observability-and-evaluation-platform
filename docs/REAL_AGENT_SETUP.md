# Real ReAct Agent Setup

`react-agent` is the first non-replay Adapter. It uses the OpenAI-compatible Chat Completions Function Calling shape and never reads credentials from a Manifest or artifact.

## Configure credentials

In the terminal that starts the Benchmark, set:

```bash
export AGENT_API_KEY="..."
export AGENT_MODEL="your-function-calling-model"
# Optional. Defaults to https://api.openai.com/v1
export AGENT_BASE_URL="https://api.openai.com/v1"
```

`AGENT_API_KEY` is used only for the HTTPS request Authorization header. The platform never serializes it into `trial-input.json`, Trace, Result, SQLite, or JSONL.

## Run a real Trial

```bash
cd <repository-root>

python3.11 scripts/run_benchmark.py \
  --adapter react-agent \
  --agent-version react-agent-v1 \
  --manifest benchmarks/smoke-case-design.yaml \
  --output-dir .runtime/react-smoke
```

The default execution is Docker Sandbox. Add `--bash` only when you want the Agent to have its Docker-backed shell tool available.

## Expected result

The Agent receives the task plus only the allowed Function tools. Each model request, Function Call, tool result, test result, Git Diff, and score is recorded in the Trial output directory. If credentials are absent or invalid, the Trial ends as `model_failed`; it does not silently fall back to Replay.
