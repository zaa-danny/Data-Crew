#!/usr/bin/env python3
"""DataCrew - multi-agent data application factory (Lab 3). Standard library only.

Phase 1: CLI skeleton, roles/tools declarations, policy engine, trace validate, replay, deploy.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ----------------------------------------------------------------- enums
ROLES = {"orchestrator", "planner", "collector", "coder", "reviewer", "integrator", "tester", "deployer"}
EVENTS = {"run_start", "plan", "handoff", "tool", "result", "review", "fix", "merge",
          "deploy", "test", "run_end"}
VERDICTS = {"approved", "changes_requested"}
STATUSES = {"ok", "step_limit", "token_limit", "unresolved", "error"}

EXIT_OK, EXIT_USAGE, EXIT_GUARD, EXIT_UNRESOLVED = 0, 1, 2, 3

USAGE = """usage: magent.py <command> [options]

commands:
  run           --spec F --workspace D --trace F [--max-steps N] [--parallel N]
                [--policy F] [--seed N] [--max-review-rounds N]
  deploy        --workspace D [--port N] [--json]
  replay        --trace F
  trace validate --trace F
  policy test   --policy F --cases F
  roles --json
  tools --json
  --help
"""


class UsageError(Exception):
    pass


# ----------------------------------------------------------------- helpers
def now_iso() -> str:
    t = datetime.now(timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def parse_flags(argv, spec):
    """spec: {flag: type}; type is str/int/bool. Returns dict; raises UsageError."""
    out, i = {}, 0
    while i < len(argv):
        a = argv[i]
        if a not in spec:
            raise UsageError(f"unknown option {a}")
        typ = spec[a]
        key = a.lstrip("-").replace("-", "_")
        if typ is bool:
            out[key] = True
            i += 1
            continue
        if i + 1 >= len(argv):
            raise UsageError(f"option {a} needs a value")
        val = argv[i + 1]
        try:
            out[key] = typ(val)
        except ValueError:
            raise UsageError(f"option {a} expects {typ.__name__}")
        i += 2
    return out


def load_json_file(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ----------------------------------------------------------------- roles / tools
def load_roles():
    return load_json_file(HERE / "roles.json")


TOOLS = [
    {"name": "http_fetch",
     "description": "Fetch an HTTP page, return status and body; only hosts in policy.allow.network_hosts are allowed",
     "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
     "requires_network": True, "sideEffect": "read"},
    {"name": "parse_html",
     "description": "Parse the table#readings rows of a previously fetched page (by url) into {site,device,day,value} records using the stdlib html.parser; returns a count and a sample",
     "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
     "requires_network": False, "sideEffect": "none"},
    {"name": "read_file",
     "description": "Read a text file inside the agent's own directory (relative path, no '..')",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
     "requires_network": False, "sideEffect": "read"},
    {"name": "glob",
     "description": "List files matching a glob pattern inside the agent's directory",
     "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]},
     "requires_network": False, "sideEffect": "read"},
    {"name": "write_file",
     "description": "Write a text file inside the agent's own directory; path must match policy.allow.write_glob",
     "parameters": {"type": "object",
                    "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"]},
     "requires_network": False, "sideEffect": "write"},
    {"name": "run_bash",
     "description": "Run a shell command inside the agent's directory; must start with an allowed prefix and match no deny pattern",
     "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
     "requires_network": False, "sideEffect": "exec"},
    {"name": "sqlite_exec",
     "description": "Execute SQL statements against a SQLite database file inside the agent's directory",
     "parameters": {"type": "object",
                    "properties": {"db": {"type": "string"}, "sql": {"type": "string"}},
                    "required": ["db", "sql"]},
     "requires_network": False, "sideEffect": "write"},
    {"name": "merge_files",
     "description": "Integrator only: copy reviewed files from an agent directory into workspace/ with provenance",
     "parameters": {"type": "object",
                    "properties": {"from_workdir": {"type": "string"},
                                   "files": {"type": "array", "items": {"type": "string"}}},
                    "required": ["from_workdir", "files"]},
     "requires_network": False, "sideEffect": "write"},
    {"name": "task",
     "description": "Orchestrator only: delegate a task to a sub-agent with an explicit handoff contract",
     "parameters": {"type": "object",
                    "properties": {"agent": {"type": "string"}, "task": {"type": "string"},
                                   "contract": {"type": "string"}},
                    "required": ["agent", "task", "contract"]},
     "requires_network": False, "sideEffect": "exec"},
]


# ----------------------------------------------------------------- policy engine
def glob_to_regex(pat: str):
    out, i = "", 0
    while i < len(pat):
        c = pat[i]
        if pat.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pat.startswith("**", i):
            out += ".*"
            i += 2
        elif c == "*":
            out += "[^/]*"
            i += 1
        elif c == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(c)
            i += 1
    return re.compile("^" + out + "$")


def glob_match(path: str, patterns) -> bool:
    return any(glob_to_regex(p).match(path) for p in patterns)


def normalize_path(path):
    """Return (normalized, error). Absolute paths and '..' escapes are rejected."""
    if not isinstance(path, str) or not path:
        return None, "empty path"
    p = path.replace("\\", "/")
    if p.startswith("/") or re.match(r"^[A-Za-z]:", p):
        return None, "absolute path"
    parts = [x for x in p.split("/") if x not in ("", ".")]
    if ".." in parts:
        return None, "path escapes workspace"
    return "/".join(parts), None


def policy_decide(policy: dict, op: dict):
    kind = op.get("op")
    allow, deny = policy.get("allow", {}), policy.get("deny", {})
    if kind in ("write_file", "read_file"):
        norm, err = normalize_path(op.get("path"))
        if err:
            return "deny", err
        if kind == "write_file":
            if glob_match(norm, allow.get("write_glob", [])):
                return "allow", "path matches write_glob"
            return "deny", "path not in write_glob"
        if glob_match(norm, deny.get("read_glob", [])):
            return "deny", "path matches deny.read_glob"
        if glob_match(norm, allow.get("read_glob", [])):
            return "allow", "path matches read_glob"
        return "deny", "path not in read_glob"
    if kind == "http_fetch":
        host = (urllib.parse.urlparse(op.get("url", "")).hostname or "").lower()
        hosts = [h.lower() for h in allow.get("network_hosts", [])]
        if host and (host in hosts or "*" in hosts):
            return "allow", "host in whitelist"
        return "deny", "host not in whitelist"
    if kind == "run_bash":
        cmd = (op.get("command") or "").strip()
        for pat in deny.get("bash_pattern", []):
            if pat in cmd:
                return "deny", f"command contains denied pattern {pat!r}"
        for pre in allow.get("bash_prefix", []):
            if cmd == pre or cmd.startswith(pre + " "):
                return "allow", f"command starts with {pre!r}"
        return "deny", "command prefix not allowed"
    return "deny", "unknown operation (default deny)"


# ----------------------------------------------------------------- trace validate
def trace_errors(rows):
    errs = []
    if not rows:
        return ["trace is empty"]
    prev, run_ids = 0, set()
    plan_tasks, handoffs, writes, merges = {}, [], set(), []
    reviews, fixes, ended = {}, set(), None
    for i, r in enumerate(rows, 1):
        w = f"line {i}"
        for key in ("seq", "ts", "run_id", "session", "agent", "role", "event"):
            if key not in r:
                errs.append(f"{w}: missing field {key}")
        if "seq" in r:
            if not isinstance(r["seq"], int) or r["seq"] <= prev:
                errs.append(f"{w}: seq must strictly increase ({prev} -> {r.get('seq')})")
            else:
                prev = r["seq"]
        if "ts" in r and not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$", str(r["ts"])):
            errs.append(f"{w}: ts is not ISO8601 UTC ({r.get('ts')})")
        if "role" in r and r["role"] not in ROLES:
            errs.append(f"{w}: illegal role {r['role']}")
        if "event" in r and r["event"] not in EVENTS:
            errs.append(f"{w}: illegal event {r['event']}")
        if "run_id" in r:
            run_ids.add(r["run_id"])
        ev = r.get("event")
        if ev == "run_start":
            if not {"spec", "provider"} <= set(r):
                errs.append(f"{w}: run_start missing spec/provider")
        elif ev == "plan":
            for t in r.get("tasks") or []:
                if not {"id", "module", "agent"} <= set(t):
                    errs.append(f"{w}: plan task missing id/module/agent")
                else:
                    plan_tasks[t["id"]] = t
        elif ev == "handoff":
            handoffs.append(r)
            if not {"to", "task", "workdir"} <= set(r):
                errs.append(f"{w}: handoff missing to/task/workdir")
        elif ev == "tool":
            if not {"tool", "workdir", "ok"} <= set(r):
                errs.append(f"{w}: tool event missing tool/workdir/ok")
            for key in ("path", "db"):
                val = r.get(key)
                if val:
                    if os.path.isabs(val) or re.match(r"^[A-Za-z]:", val) or ".." in Path(val).parts:
                        errs.append(f"{w}: {key} escapes workspace: {val}")
                    writes.add((r.get("workdir"), val))
        elif ev == "result":
            if not {"task", "ok"} <= set(r):
                errs.append(f"{w}: result missing task/ok")
        elif ev == "review":
            if r.get("verdict") not in VERDICTS:
                errs.append(f"{w}: illegal verdict {r.get('verdict')}")
            else:
                reviews.setdefault(r.get("task"), []).append(r["verdict"])
            for d in r.get("defects") or []:
                if not {"file", "why"} <= set(d):
                    errs.append(f"{w}: defect missing file/why")
        elif ev == "fix":
            fixes.add(r.get("task"))
        elif ev == "merge":
            if not {"from_workdir", "files"} <= set(r):
                errs.append(f"{w}: merge missing from_workdir/files")
            merges.append(r)
        elif ev == "deploy":
            if not {"command", "port", "health"} <= set(r):
                errs.append(f"{w}: deploy missing command/port/health")
        elif ev == "test":
            if not {"suite", "passed", "failed", "ok"} <= set(r):
                errs.append(f"{w}: test missing suite/passed/failed/ok")
        elif ev == "run_end":
            if r.get("status") not in STATUSES:
                errs.append(f"{w}: illegal status {r.get('status')}")
            ended = r
    if len(run_ids) > 1:
        errs.append(f"multiple run_id in one trace: {sorted(run_ids)}")
    if ended is None:
        errs.append("missing run_end event")
    if not plan_tasks:
        errs.append("missing plan event or empty task list")
    for h in handoffs:
        t = plan_tasks.get(h.get("task"))
        if t and h.get("to") != t["agent"]:
            errs.append(f"handoff target {h.get('to')} differs from plan agent {t['agent']}")
    for m in merges:
        for f in m.get("files") or []:
            if (m.get("from_workdir"), f) not in writes:
                errs.append(f"merge file {f} has no write record under {m.get('from_workdir')} (no provenance)")
    for task, seq in reviews.items():
        if "changes_requested" in seq:
            if task not in fixes:
                errs.append(f"task {task} was rejected but has no fix record")
            elif seq[-1] != "approved":
                errs.append(f"task {task} was fixed but the final review is not approved")
    return errs


def read_jsonl(path):
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


# ----------------------------------------------------------------- replay
def replay_rows(rows):
    start = next((r for r in rows if r.get("event") == "run_start"), rows[0] if rows else {})
    run_id = start.get("run_id")
    agents = {}
    for r in rows:
        name = r.get("agent")
        if name is None:
            continue
        a = agents.setdefault(name, {"name": name, "role": r.get("role"), "tasks": set(), "tool_calls": 0})
        if r.get("event") == "tool":
            a["tool_calls"] += 1
    for r in rows:
        if r.get("event") == "handoff" and r.get("to") in agents:
            agents[r["to"]]["tasks"].add(r.get("task"))
    agent_list = [{"name": a["name"], "role": a["role"], "tasks": len(a["tasks"]),
                   "tool_calls": a["tool_calls"]} for a in sorted(agents.values(), key=lambda x: x["name"])]

    plan = next((r for r in rows if r.get("event") == "plan"), {})
    tasks = []
    for t in plan.get("tasks") or []:
        tid = t["id"]
        res = [r for r in rows if r.get("event") == "result" and r.get("task") == tid]
        revs = [r for r in rows if r.get("event") == "review" and r.get("task") == tid]
        fxs = [r for r in rows if r.get("event") == "fix" and r.get("task") == tid]
        if res and res[-1].get("ok") is True:
            status = "done"
        elif res:
            status = "failed"
        else:
            status = "pending"
        tasks.append({"id": tid, "agent": t["agent"], "reviews": len(revs), "fixes": len(fxs), "status": status})

    artifacts = sorted({f for r in rows if r.get("event") == "merge" for f in r.get("files") or []})
    dep = [r for r in rows if r.get("event") == "deploy"]
    tst = [r for r in rows if r.get("event") == "test"]
    end = next((r for r in reversed(rows) if r.get("event") == "run_end"), {})
    return {
        "run_id": run_id,
        "status": end.get("status"),
        "agents": agent_list,
        "tasks": tasks,
        "artifacts": artifacts,
        "deploy": {"port": dep[-1].get("port"), "health": dep[-1].get("health")} if dep else None,
        "tests": {"passed": tst[-1].get("passed"), "failed": tst[-1].get("failed")} if tst else None,
    }


# ----------------------------------------------------------------- deploy
def do_deploy(workspace: Path, port, as_json):
    app = workspace / "server" / "app.py"
    if not app.is_file():
        print(json.dumps({"ok": False, "error": f"{app} not found"}))
        return EXIT_USAGE
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    proc = subprocess.Popen([sys.executable, "server/app.py", "--port", str(port or 0)], cwd=str(workspace),
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, **kw)
    actual = None
    deadline = time.time() + 15
    while time.time() < deadline and actual is None:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                break
            continue
        m = re.match(r"LISTENING (\d+)", line.strip())
        if m:
            actual = int(m.group(1))
    healthy = False
    if actual:
        import http.client
        for _ in range(40):
            try:
                c = http.client.HTTPConnection("127.0.0.1", actual, timeout=2)
                c.request("GET", "/api/health")
                resp = c.getresponse()
                body = resp.read().decode("utf-8", "replace")
                c.close()
                if resp.status == 200 and json.loads(body).get("ok") is True:
                    healthy = True
                    break
            except Exception:
                time.sleep(0.25)
    if not healthy:
        proc.terminate()
        print(json.dumps({"ok": False, "port": actual, "health": False}))
        return EXIT_USAGE
    out = {"ok": True, "port": actual, "url": f"http://127.0.0.1:{actual}/", "health": True}
    print(json.dumps(out))
    return EXIT_OK


# ----------------------------------------------------------------- run (engine added in phase 2)
def do_run(opts):
    from engine import run_crew  # phase 2 module
    return run_crew(opts)


def run_zero_budget(opts):
    """--max-steps 0: exit 2 immediately and leave run_end{status:step_limit} in the trace."""
    spec = str(opts.get("spec"))
    run_id = "run-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(3).hex()
    trace = Path(opts.get("trace", "trace.jsonl"))
    base = {"run_id": run_id, "session": "sess-orch", "agent": "orchestrator", "role": "orchestrator"}
    rows = [
        dict(base, seq=1, ts=now_iso(), event="run_start", spec=spec, provider={"name": "none", "model": "none"}),
        dict(base, seq=2, ts=now_iso(), event="run_end", status="step_limit", steps=0, tokens=0),
    ]
    trace.parent.mkdir(parents=True, exist_ok=True)
    trace.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return EXIT_GUARD


# ----------------------------------------------------------------- main
def main(argv):
    if not argv or argv[0] in ("--help", "-h", "help"):
        sys.stderr.write(USAGE)
        return EXIT_OK
    cmd, rest = argv[0], argv[1:]
    try:
        if cmd == "run":
            o = parse_flags(rest, {"--spec": str, "--workspace": str, "--trace": str, "--max-steps": int,
                                   "--parallel": int, "--policy": str, "--seed": int,
                                   "--max-review-rounds": int, "--max-tokens": int})
            if "spec" not in o:
                raise UsageError("run requires --spec")
            if not Path(o["spec"]).is_file():
                raise UsageError(f"cannot read spec: {o['spec']}")
            try:
                load_json_file(o["spec"])
            except Exception as exc:
                raise UsageError(f"spec is not valid JSON: {exc}")
            o.setdefault("workspace", "workspace")
            o.setdefault("trace", "trace.jsonl")
            o.setdefault("max_steps", 80)
            o.setdefault("parallel", 4)
            if o["max_steps"] <= 0:
                return run_zero_budget(o)
            return do_run(o)
        if cmd == "deploy":
            o = parse_flags(rest, {"--workspace": str, "--port": int, "--json": bool})
            if "workspace" not in o:
                raise UsageError("deploy requires --workspace")
            return do_deploy(Path(o["workspace"]).resolve(), o.get("port"), o.get("json"))
        if cmd == "replay":
            o = parse_flags(rest, {"--trace": str})
            if "trace" not in o:
                raise UsageError("replay requires --trace")
            print(json.dumps(replay_rows(read_jsonl(o["trace"])), indent=2))
            return EXIT_OK
        if cmd == "trace":
            if not rest or rest[0] != "validate":
                raise UsageError("usage: trace validate --trace F")
            o = parse_flags(rest[1:], {"--trace": str})
            if "trace" not in o:
                raise UsageError("trace validate requires --trace")
            try:
                rows = read_jsonl(o["trace"])
            except Exception as exc:
                print(json.dumps({"ok": False, "events": 0, "errors": [f"cannot parse trace: {exc}"]}))
                return EXIT_USAGE
            errs = trace_errors(rows)
            print(json.dumps({"ok": not errs, "events": len(rows), "errors": errs}))
            return EXIT_OK if not errs else EXIT_USAGE
        if cmd == "policy":
            if not rest or rest[0] != "test":
                raise UsageError("usage: policy test --policy F --cases F")
            o = parse_flags(rest[1:], {"--policy": str, "--cases": str})
            if "policy" not in o or "cases" not in o:
                raise UsageError("policy test requires --policy and --cases")
            policy, cases = load_json_file(o["policy"]), load_json_file(o["cases"])
            results = []
            for c in cases:
                decision, reason = policy_decide(policy, c)
                results.append({"id": c.get("id"), "decision": decision, "reason": reason})
            print(json.dumps({"results": results}))
            return EXIT_OK
        if cmd == "roles":
            parse_flags(rest, {"--json": bool})
            print(json.dumps(load_roles(), indent=2))
            return EXIT_OK
        if cmd == "tools":
            parse_flags(rest, {"--json": bool})
            print(json.dumps(TOOLS, indent=2))
            return EXIT_OK
        raise UsageError(f"unknown command: {cmd}")
    except UsageError as exc:
        sys.stderr.write(f"error: {exc}\n{USAGE}")
        return EXIT_USAGE
    except Exception as exc:  # uncaught internal error
        sys.stderr.write(f"internal error: {type(exc).__name__}: {exc}\n")
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
