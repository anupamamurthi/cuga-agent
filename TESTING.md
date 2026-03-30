# Testing Guide: CUGA and CUGA++

This guide covers how to run tests for **CUGA** (the agent framework) and **CUGA++** (the skills/plugin system) — separately and together.

---

## Overview

| Component | Location | Test runner | LLM required? |
|---|---|---|---|
| CUGA | `cuga-agent-mar30/` | `uv run pytest` via `run_tests.sh` | Yes (most tests) |
| CUGA++ | `~/Desktop/cuga++/` | `pytest` | No |
| Integration | CUGA + linked CUGA++ | `uv run pytest` | Yes (CUGA tests) |

CUGA's `pyproject.toml` already links to CUGA++ via local editable paths:

```toml
[tool.uv.sources]
cuga-plugin-sdk = { path = "../../../Desktop/cuga++/packages/cuga-plugin-sdk", editable = true }
cuga-skills     = { path = "../../../Desktop/cuga++/packages/cuga-skills",      editable = true }
```

So `uv sync` inside CUGA picks up the live CUGA++ source automatically.

---

## Prerequisites

- Python 3.10–3.13
- [`uv`](https://docs.astral.sh/uv/) installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- An LLM API key for CUGA tests (e.g. `OPENAI_API_KEY`, `WATSONX_API_KEY`)
- Optional: Docker/Podman for sandbox tests, `E2B_API_KEY` for E2B executor tests

---

## Testing CUGA Standalone

```bash
cd ~/Documents/GitHub/cuga-agent-mar30

# One-time setup
uv venv --python=3.12
source .venv/bin/activate
uv sync
```

### Run all tests

```bash
./src/scripts/run_tests.sh
```

This runs in order: lint → unit → policy integration → SDK integration → manager API → memory → stability.

### Run unit tests only (no LLM needed for most)

```bash
./src/scripts/run_tests.sh unit_tests
```

Covers: OpenAPI/MCP registry, variables manager, local sandbox, E2B lite executor.

### Skip stability tests

```bash
./src/scripts/run_tests.sh --skip-stability
```

### Run a specific test suite directly

```bash
# Registry (tool loading, OpenAPI/MCP)
uv run pytest src/cuga/backend/tools_env/registry/tests/ -v

# Policy system (intent guard, playbook, tool approval, output formatter)
uv run pytest src/cuga/backend/cuga_graph/policy/tests/ -v

# SDK integration (agent invocation, streaming, multi-agent)
uv run pytest src/cuga/sdk_core/tests/ -v

# Manager API
uv run pytest tests/system/test_manager_api_integration.py -v

# Memory (requires --extra memory)
uv sync --extra memory
uv run pytest src/system_tests/e2e/test_memory_integration.py -v
```

### Run a single test function

```bash
uv run pytest src/cuga/sdk_core/tests/test_sdk_policies.py::test_tool_approval_policy_basic -v
```

---

## Testing CUGA++ Standalone

CUGA++ has zero external dependencies and does not need an LLM.

```bash
cd ~/Desktop/cuga++

# One-time setup (installs both packages in editable mode)
pip install -e packages/cuga-plugin-sdk -e "packages/cuga-skills[dev]"

# Run all tests
pytest -v

# Or target a specific package
pytest packages/cuga-plugin-sdk/tests/ -v
pytest packages/cuga-skills/tests/ -v
```

The workspace `pyproject.toml` configures `testpaths` to cover both packages automatically.

### With coverage

```bash
pytest packages/cuga-skills/tests/ \
  --cov=packages/cuga-skills/src/cuga_skills \
  --cov-report=term-missing -v
```

---

## Testing CUGA + CUGA++ Together

Because CUGA's `pyproject.toml` points at the local CUGA++ source, any change in `~/Desktop/cuga++/` is immediately reflected in the CUGA environment after `uv sync`.

```bash
cd ~/Documents/GitHub/cuga-agent-mar30

# Sync to pick up latest CUGA++ source
uv sync

# Run the SDK tests — these exercise CugaAgent with plugins
uv run pytest src/cuga/sdk_core/tests/ -v

# Or run the full suite
./src/scripts/run_tests.sh --skip-stability
```

### Verifying the integration manually

```python
from cuga.sdk import CugaAgent
from cuga_skills import CugaSkillsPlugin

agent = CugaAgent(
    tools=[...],
    plugins=[CugaSkillsPlugin(skills_dir="./my_skills")]
)
result = await agent.invoke("...")
```

Skills in `./my_skills/*.md` are injected into the agent's prompt context at invocation time.

---

## Test Suite Reference

### CUGA

| Suite | Path | What it covers |
|---|---|---|
| Registry | `src/cuga/backend/tools_env/registry/tests/` | OpenAPI spec loading, MCP integration |
| Variables manager | `src/cuga/backend/cuga_graph/nodes/api/variables_manager/tests/` | State variable handling |
| Executors | `src/cuga/backend/cuga_graph/nodes/cuga_lite/executors/tests/` | Local + E2B code execution |
| Policy integration | `src/cuga/backend/cuga_graph/policy/tests/` | Intent guard, playbook, tool approval, output formatter, NL trigger resolution |
| SDK integration | `src/cuga/sdk_core/tests/` | Agent invocation, streaming, policies, multi-agent supervisor, YAML config |
| Manager API | `tests/system/test_manager_api_integration.py` | Draft vs production mode, tool isolation |
| Memory | `src/system_tests/` | CLI, e2e memory integration, balanced mode |
| Stability | `run_stability_tests.py` | Fast mode, CRM workflows, HuggingFace utterances |

### CUGA++

| Suite | Path | What it covers |
|---|---|---|
| Plugin SDK | `packages/cuga-plugin-sdk/tests/unit/test_protocol.py` | `CugaPlugin` duck-typing, `PromptContext`, `PromptContribution` |
| Skills loader | `packages/cuga-skills/tests/unit/test_loader.py` | Load from directory, ordering, merging, edge cases |

---

## Lint and Format

CUGA runs ruff as part of the test script. To run manually:

```bash
uv run ruff check          # lint
uv run ruff format --check # format check
uv run ruff format         # auto-format
```

---

## Troubleshooting

**`uv sync` can't find cuga-plugin-sdk or cuga-skills**
: Check that `~/Desktop/cuga++/packages/` exists and both packages have a `pyproject.toml`. The path in `[tool.uv.sources]` is relative to the CUGA repo root.

**E2B executor tests skipped**
: Set `E2B_API_KEY` in your environment, then `uv sync --extra e2b` before running.

**Memory tests fail on import**
: Run `uv sync --extra memory` before the memory test suite.

**Port conflicts when running manager API tests**
: Kill any running `cuga start` processes before running `test_manager_api_integration.py`.

**Stability tests time out**
: These run full agent loops locally. Ensure your LLM API key is set and rate limits aren't hit. Pass `--method docker` to `run_stability_tests.py` to run in a container instead.
