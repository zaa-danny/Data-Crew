# DataCrew - Lab 3 multi-agent data application factory

Student: 穆达恩 (Danny) | ID 2528008 | Class NEUsoft

DataCrew reads a `spec.json` and runs a team of agents (collector, three coders, reviewer, integrator,
tester, deployer) that collect site data over HTTP, analyze it, store it in SQLite, build a dashboard and a
backend API, deploy it and self-test it. Roles, tools, permissions and the trace follow the Lab 3 contract.

## 1. Environment

| Item | Value |
|---|---|
| OS used | Windows 11 + Anaconda Prompt |
| Python | 3.10 or newer (tested with 3.11) |
| Dependencies | **None.** Standard library only; there is nothing to `pip install` |

```
conda create -n datacrew python=3.11 -y
conda activate datacrew
python --version
```

## 2. Model configuration (a real model is required)

`config.json` is the only place where the model is chosen. **The API key is never stored in a file**: it is read
from the environment variable named in `api_key_env`.

Online API (used for the submitted runs): DeepSeek `deepseek-chat`, OpenAI-compatible protocol.

```json
{
  "provider": "openai_compatible",
  "base_url": "https://api.deepseek.com/v1",
  "model": "deepseek-chat",
  "api_key_env": "DEEPSEEK_API_KEY",
  "api_key": "",
  "temperature": 0
}
```

Set the key in the same Anaconda Prompt window that runs the commands (Windows `cmd`):

```
set DEEPSEEK_API_KEY=<your key>
set MAGENT_APPROVE=auto
```

Local alternative (Ollama): set `base_url` to `http://127.0.0.1:11434/v1` and `model` to your Ollama model.
Ollama's default context window is too small for the agent instructions, so create a copy with `num_ctx 8192`:

```
echo FROM qwen2.5-coder:7b> Modelfile
echo PARAMETER num_ctx 8192>> Modelfile
ollama create qwen2.5-coder-8k -f Modelfile
```

(In my tests the local 7B model could not finish the collector task; the submitted runs use DeepSeek.)

## 3. One-command run and acceptance

Put `verify.py` and `fixtures/` next to `magent.py`, then:

```
python verify.py magent.py --both
```

- 13 offline contract checks (no model needed)
- 22 live checks against a random data source (needs the API key)
- `python verify.py magent.py --live --seeds 3` runs three consecutive random specs

## 4. Manual sample run (produces trace.jsonl and run_report.json)

Window A - fixed sample data source (leave it open):

```
cd fixtures\source
python -m http.server 8000
```

Window B:

```
mkdir runs\sample
cd runs\sample
python ..\..\magent.py run --spec ..\..\fixtures\spec\sample.spec.json --workspace workspace --trace trace.jsonl --max-steps 80 --parallel 4 --policy ..\..\fixtures\policy\policy.json
```

Output of a run directory: `trace.jsonl`, `run_report.json`, `agents/<name>/` (one isolated directory per agent) and
`workspace/` (the final project, written only by the integrator).

## 5. Commands

```
python magent.py run --spec F --workspace D --trace F [--max-steps N] [--parallel N] [--policy F] [--seed N] [--max-review-rounds N]
python magent.py deploy --workspace D [--port N] [--json]
python magent.py replay --trace F
python magent.py trace validate --trace F
python magent.py policy test --policy F --cases F
python magent.py roles --json
python magent.py tools --json
python magent.py --help
```

Exit codes: 0 success, 1 usage/internal error or failed tasks, 2 step/token budget exhausted, 3 review not closed.

## 6. Project files

| File | Purpose |
|---|---|
| `magent.py` | CLI entry point, policy engine, trace validation, replay, deploy |
| `engine.py` | provider, agent loop, tools, DAG scheduler, review loop, integrator merge, deploy and self-test |
| `roles.json` | declarative roles (system prompt, allowed tools, read-only flag, max steps, modules, dependencies) |
| `config.json` | model/provider configuration (key left empty) |

## 7. Design notes

- Roles are data in `roles.json`; only the orchestrator has the `task` tool (no recursive spawning).
- Every handoff goes out before any agent starts working; ready tasks then run in parallel as a DAG
  (collect -> analyze -> api / ui -> qa -> deploy).
- Each agent works only in `agents/<name>/`; writes are checked against `policy.json` and the task's owned files.
- The reviewer is read-only; rejected tasks get a rework handoff and a `fix` event until approved.
- The integrator is the single write gateway to `workspace/` and records per-file provenance in `merge` events.
- Files created by a script run through `run_bash` are recorded in the trace as `write_file` events with `"via": "run_bash"`.
