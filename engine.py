"""DataCrew engine: provider, agent loop, tools, DAG scheduler, review loop, merge, deploy, self-test.

Roles are data (roles.json). Sub-agents never get the `task` tool. Only the integrator writes workspace/.
"""
from __future__ import annotations

import fnmatch
import html.parser
import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path

import magent as M

HERE = Path(__file__).resolve().parent

DEFAULT_POLICY = {
    "version": 2, "workspace_root": ".", "default": "deny",
    "allow": {
        "write_glob": ["data/**", "site/**", "server/**", "tests/**", "deploy/**", "pipeline/**",
                       "collector/**", "agents/**", "README.md"],
        "read_glob": ["**"],
        "bash_prefix": ["python", "python3", "ls", "cat", "git status", "git diff"],
        "network_hosts": ["127.0.0.1", "localhost"],
    },
    "deny": {"bash_pattern": ["rm -rf /", "curl | sh", "chmod 777 /", ":(){"],
             "read_glob": ["**/.env", "**/api-keys.txt", "**/*.pem"]},
    "escape": "deny",
}

SCRATCH_GLOBS = ["**/_*.py", "**/_*.txt"]  # underscore-prefixed helper files: writable, never merged
MAX_TOOLS_PER_TURN = 12


class BudgetExceeded(Exception):
    def __init__(self, status):
        super().__init__(status)
        self.status = status


class ProviderError(Exception):
    pass


# ------------------------------------------------------------------ provider
class Provider:
    def __init__(self, cfg):
        self.cfg = cfg
        self.base = cfg["base_url"].rstrip("/")
        self.model = cfg["model"]
        key = os.environ.get(cfg.get("api_key_env", ""), "") or cfg.get("api_key", "")
        if not key and "127.0.0.1" not in self.base and "localhost" not in self.base:
            raise ProviderError(
                f"API key not found: set the environment variable {cfg.get('api_key_env')}")
        self.key = key or "local"
        host = urllib.parse.urlparse(self.base).hostname or "provider"
        self.name = "ollama" if "11434" in self.base else host.replace("api.", "").split(".")[0]

    def chat(self, messages):
        body = json.dumps({"model": self.model, "messages": messages,
                           "temperature": self.cfg.get("temperature", 0)}).encode()
        req = urllib.request.Request(self.base + "/chat/completions", data=body, method="POST", headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})
        last = None
        local = (urllib.parse.urlparse(self.base).hostname or "") in ("127.0.0.1", "localhost")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if local else urllib.request.build_opener()
        for attempt in range(3):
            try:
                with opener.open(req, timeout=180) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                text = data["choices"][0]["message"].get("content") or ""
                tokens = int((data.get("usage") or {}).get("total_tokens") or 0)
                return text, tokens
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:300]
                if exc.code in (401, 403):
                    raise ProviderError(f"HTTP {exc.code} from provider (check API key / model): {detail}")
                last = f"HTTP {exc.code}: {detail}"
                if exc.code not in (429, 500, 502, 503, 504):
                    raise ProviderError(last)
            except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError) as exc:
                last = f"{type(exc).__name__}: {exc}"
            time.sleep(2 * (attempt + 1))
        raise ProviderError(f"provider failed after retries: {last}")


# ------------------------------------------------------------------ trace & budget
class Trace:
    def __init__(self, path: Path, run_id: str):
        self.path, self.run_id, self.seq = path, run_id, 0
        self.lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(path, "w", encoding="utf-8")

    def emit(self, agent, role, event, **fields):
        with self.lock:
            self.seq += 1
            row = {"seq": self.seq, "ts": M.now_iso(), "run_id": self.run_id, "session": "sess-" + agent,
                   "agent": agent, "role": role, "event": event}
            row.update(fields)
            self.f.write(json.dumps(row, ensure_ascii=False) + "\n")
            self.f.flush()

    def close(self):
        self.f.close()


class Ctx:
    pass


# ------------------------------------------------------------------ HTML parsing tool
class ReadingsParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.site, self.rows = None, []
        self._in_h1 = False
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "h1" and "data-site" in a:
            self.site = a["data-site"]
        elif tag == "tr" and "data-device" in a:
            self._row = {"device": a["data-device"], "day": a.get("data-day"), "value": None}
        elif tag == "td" and self._row is not None:
            self._cell = (a.get("class") or "").split()

    def handle_data(self, data):
        if self._row is not None and self._cell and "value" in self._cell and data.strip():
            self._row["value"] = float(data.strip())

    def handle_endtag(self, tag):
        if tag == "td":
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row["value"] is not None:
                self.rows.append(self._row)
            self._row = None


def parse_readings(text):
    p = ReadingsParser()
    p.feed(text)
    return [{"site": p.site, "device": r["device"], "day": r["day"], "value": r["value"]} for r in p.rows]


# ------------------------------------------------------------------ tools
def snapshot(root: Path):
    snap = {}
    for p in root.rglob("*"):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
            st = p.stat()
            snap[p.relative_to(root).as_posix()] = (st.st_mtime_ns, st.st_size)
    return snap


def clip(text, n=2500):
    return text if len(text) <= n else text[:n] + f"\n...[truncated {len(text) - n} chars]"


class AgentTools:
    """Executes one agent's tool calls inside its own working directory."""

    def __init__(self, ctx, agent, role_cfg, workdir_rel, task):
        self.ctx, self.agent, self.cfg = ctx, agent, role_cfg
        self.rel = workdir_rel
        self.root = ctx.run_dir / workdir_rel
        self.task = task
        self.written = task["written"]
        self.fetched = {}

    def _path_ok(self, op, path):
        norm, err = M.normalize_path(path)
        if err:
            return None, err
        decision, reason = M.policy_decide(self.ctx.policy, {"op": op, "path": norm})
        if decision != "allow":
            return None, f"policy deny: {reason}"
        return norm, None

    def _writable(self, norm):
        if M.glob_match(norm, self.task["owned"]) or M.glob_match(norm, SCRATCH_GLOBS):
            return None
        return f"{norm} is not in this task's allowed files {self.task['owned']}"

    def record_write(self, norm, tool="write_file", via=None):
        if norm not in self.written:
            self.written.append(norm)
        extra = {"db": norm} if tool == "sqlite_exec" else {"path": norm}
        if via:
            extra["via"] = via
        self.ctx.trace.emit(self.agent, self.cfg["role"], "tool", tool=tool, workdir=self.rel, ok=True, **extra)

    def run(self, name, args):
        """Return (ok, text). Emits the trace event itself."""
        t = self.ctx.trace
        role = self.cfg["role"]
        if name not in self.cfg["allowedTools"] or name in ("task", "merge_files"):
            t.emit(self.agent, role, "tool", tool=name, workdir=self.rel, ok=False, error="tool not allowed for role")
            return False, f"tool {name} is not allowed for your role (allowed: {self.cfg['allowedTools']})"
        try:
            fn = getattr(self, "t_" + name)
        except AttributeError:
            t.emit(self.agent, role, "tool", tool=name, workdir=self.rel, ok=False, error="unknown tool")
            return False, f"unknown tool {name}"
        try:
            return fn(args)
        except Exception as exc:  # tool crash is fed back, never sinks the run
            t.emit(self.agent, role, "tool", tool=name, workdir=self.rel, ok=False, error=str(exc)[:200])
            return False, f"{name} failed: {type(exc).__name__}: {exc}"

    # -- individual tools
    def t_http_fetch(self, a):
        url = str(a.get("url", ""))
        t, role = self.ctx.trace, self.cfg["role"]
        decision, reason = M.policy_decide(self.ctx.policy, {"op": "http_fetch", "url": url})
        if decision != "allow":
            t.emit(self.agent, role, "tool", tool="http_fetch", url=url, workdir=self.rel, ok=False,
                   error=f"policy deny: {reason}")
            return False, f"policy deny: {reason}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(url, timeout=20) as resp:
                body = resp.read().decode("utf-8", "replace")
                status = resp.status
        except Exception as exc:
            t.emit(self.agent, role, "tool", tool="http_fetch", url=url, workdir=self.rel, ok=False, error=str(exc)[:200])
            return False, f"fetch failed: {exc}"
        self.fetched[url] = body
        ok = 200 <= status < 300
        t.emit(self.agent, role, "tool", tool="http_fetch", url=url, workdir=self.rel, ok=ok, status=status)
        return ok, f"status {status}, {len(body)} chars. Preview:\n{clip(body, 1800)}"

    def t_parse_html(self, a):
        url = str(a.get("url", ""))
        t, role = self.ctx.trace, self.cfg["role"]
        body = self.fetched.get(url)
        if body is None:
            t.emit(self.agent, role, "tool", tool="parse_html", workdir=self.rel, ok=False, error="url not fetched")
            return False, "fetch the url with http_fetch first"
        recs = parse_readings(body)
        t.emit(self.agent, role, "tool", tool="parse_html", workdir=self.rel, ok=True, url=url)
        return True, f"{len(recs)} records; first 3: {json.dumps(recs[:3])}"

    def t_read_file(self, a):
        t, role = self.ctx.trace, self.cfg["role"]
        norm, err = self._path_ok("read_file", str(a.get("path", "")))
        if err:
            t.emit(self.agent, role, "tool", tool="read_file", workdir=self.rel, ok=False, attempted_path=str(a.get("path")), error=err)
            return False, err
        p = self.root / norm
        if not p.is_file():
            t.emit(self.agent, role, "tool", tool="read_file", workdir=self.rel, ok=False, path=norm, error="not found")
            return False, f"{norm} not found"
        text = p.read_text(encoding="utf-8", errors="replace")
        t.emit(self.agent, role, "tool", tool="read_file", workdir=self.rel, ok=True, path=norm)
        return True, clip(text, 6000)

    def t_glob(self, a):
        t, role = self.ctx.trace, self.cfg["role"]
        pat = str(a.get("pattern", "**/*"))
        if ".." in pat or pat.startswith("/"):
            t.emit(self.agent, role, "tool", tool="glob", workdir=self.rel, ok=False, error="pattern escapes")
            return False, "pattern escapes working directory"
        rx = M.glob_to_regex(pat)
        files = sorted(k for k in snapshot(self.root) if rx.match(k))[:200]
        t.emit(self.agent, role, "tool", tool="glob", workdir=self.rel, ok=True, pattern=pat)
        return True, "\n".join(files) or "(no match)"

    def t_write_file(self, a):
        t, role = self.ctx.trace, self.cfg["role"]
        raw = str(a.get("path", ""))
        norm, err = self._path_ok("write_file", raw)
        if not err:
            err = self._writable(norm)
        if err:
            t.emit(self.agent, role, "tool", tool="write_file", workdir=self.rel, ok=False, attempted_path=raw, error=err)
            return False, err
        content = a.get("content")
        if not isinstance(content, str):
            t.emit(self.agent, role, "tool", tool="write_file", workdir=self.rel, ok=False, path=norm, error="no content")
            return False, "write_file needs content (use the <<< ... >>> block)"
        p = self.root / norm
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        self.record_write(norm)
        return True, f"wrote {norm} ({len(content)} chars)"

    def t_sqlite_exec(self, a):
        t, role = self.ctx.trace, self.cfg["role"]
        raw = str(a.get("db", ""))
        norm, err = self._path_ok("write_file", raw)
        if not err:
            err = self._writable(norm)
        if err:
            t.emit(self.agent, role, "tool", tool="sqlite_exec", workdir=self.rel, ok=False, attempted_path=raw, error=err)
            return False, err
        sql = a.get("sql") or a.get("content") or ""
        p = self.root / norm
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(p))
        try:
            if sql.strip().lower().startswith("select"):
                rows = conn.execute(sql).fetchmany(20)
                out = json.dumps(rows)
            else:
                conn.executescript(sql)
                conn.commit()
                out = "ok"
        finally:
            conn.close()
        self.record_write(norm, tool="sqlite_exec")
        return True, out

    def t_run_bash(self, a):
        t, role = self.ctx.trace, self.cfg["role"]
        cmd = str(a.get("command", "")).strip()
        decision, reason = M.policy_decide(self.ctx.policy, {"op": "run_bash", "command": cmd})
        if decision != "allow":
            t.emit(self.agent, role, "tool", tool="run_bash", command=cmd, workdir=self.rel, ok=False, error=f"policy deny: {reason}")
            return False, f"policy deny: {reason}"
        if not self.ctx.approve("run_bash", f"{self.agent}: {cmd}"):
            t.emit(self.agent, role, "tool", tool="run_bash", command=cmd, workdir=self.rel, ok=False, error="not approved")
            return False, "command was not approved by the user"
        before = snapshot(self.root)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
        try:
            r = subprocess.run(cmd, shell=True, cwd=str(self.root), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=90, env=env)
            code, out = r.returncode, (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr.strip() else "")
        except subprocess.TimeoutExpired:
            code, out = 124, "TIMEOUT after 90s"
        t.emit(self.agent, role, "tool", tool="run_bash", command=cmd, workdir=self.rel, ok=(code == 0), exit_code=code)
        after = snapshot(self.root)
        for rel_path, sig in sorted(after.items()):
            if before.get(rel_path) != sig and (M.glob_match(rel_path, self.task["owned"])
                                                or M.glob_match(rel_path, SCRATCH_GLOBS)):
                self.record_write(rel_path, tool="sqlite_exec" if rel_path.endswith(".db") else "write_file", via="run_bash")
        return code == 0, f"exit {code}\n{clip(out)}"


# ------------------------------------------------------------------ reply protocol
PROTOCOL = """
TOOL PROTOCOL (follow exactly). You work in your own directory; use RELATIVE paths only.
Reply with one or more tool calls, each on its own line, in this form:
@tool <name> <JSON arguments on ONE line>
For write_file (and multi-line sqlite_exec sql) put the content between a line `<<<` and a line `>>>`:
@tool write_file {"path": "pipeline/example.py"}
<<<
file content here
>>>
Several @tool calls may be sent in one reply; they run in order and you receive every result.
Wait for results before concluding. When the work is finished and verified, reply with ONE line:
@final <short conclusion: what you produced and anything still unverified>
Never paste raw HTML or whole files into the conclusion. Helper scripts must be named with a leading underscore
(for example _check.py) and are never delivered; prefer helper scripts over python -c with complex quoting.
"""


def parse_reply(text):
    lines = text.splitlines()
    n, i = len(lines), 0
    calls, errs, final = [], [], None
    while i < n:
        ln = lines[i].strip()
        m = re.match(r"^@tool\s+([A-Za-z_]\w*)\s*(.*)$", ln)
        if m:
            name, rest = m.group(1), m.group(2).strip()
            args = {}
            if rest:
                try:
                    args = json.loads(rest)
                    if not isinstance(args, dict):
                        raise ValueError("arguments must be a JSON object")
                except ValueError as exc:
                    errs.append(f"bad JSON arguments for {name}: {exc}")
                    i += 1
                    continue
            if i + 1 < n and lines[i + 1].strip() == "<<<":
                j, buf = i + 2, []
                while j < n and lines[j].strip() != ">>>":
                    buf.append(lines[j])
                    j += 1
                key = "sql" if name == "sqlite_exec" else "content"
                args[key] = "\n".join(buf) + "\n"
                i = j + 1
            else:
                i += 1
            calls.append((name, args))
            continue
        if ln.startswith("@final"):
            final = "\n".join([ln[len("@final"):].strip()] + lines[i + 1:]).strip() or "done"
            break
        i += 1
    return calls, final, errs


def extract_json(text):
    text = re.sub(r"```(?:json)?", "", text)
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for k in range(start, len(text)):
            c = text[k]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:k + 1])
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return None


# ------------------------------------------------------------------ contracts (handoff content)
METHOD = """
DATA CONTRACT (exact semantics; the checker compares field by field):
Record: {"site": str, "device": str, "day": str, "value": float}. records.jsonl keeps EVERY record (no filtering, no rounding).
Params come from params.json at run time: unit, min_value, top_k, round_digits. NEVER hardcode a threshold, precision, top_k,
site name, device name or ordering assumption.
summary.json keys: unit, record_count, kept_count, site_count, device_count, min_value, round_digits, top_k, total, mean,
max, min, by_site, top_devices.
 - kept = records with value >= min_value (only kept records enter statistics)
 - record_count = number of ALL records (before filtering); kept_count = len(kept)
 - site_count = distinct sites among kept; device_count = distinct (site, device) among kept
 - total = round(sum of raw kept values, round_digits)  (sum raw values first, round once at the end)
 - mean = round(total / kept_count, round_digits)   (use the ROUNDED total; 0.0 when nothing is kept)
 - by_site: list of {site, total, mean, device_count, kept_count}; site total = round(sum raw kept, round_digits);
   mean = round(site_total / site_kept_count, round_digits); sort by total DESCENDING, ties by site ASCENDING;
   omit sites with no kept record
 - top_devices: per (site, device): {site, device, total, mean} (total = round(sum raw kept), mean = round(sum raw / count));
   sort by total DESCENDING, ties by site ASC, then device ASC; keep the first top_k
 - max / min: the kept record ({site, device, day, value}) with the largest / smallest value;
   ties resolved by the lexicographically smallest (site, device, day)
SQLite app.db: table records(site TEXT, device TEXT, day TEXT, value REAL, PRIMARY KEY(site, device, day)) holding ALL
records; table summary(key TEXT PRIMARY KEY, value TEXT) with at least total, mean, record_count (and ideally every
top-level scalar key). Must be idempotent: DROP TABLE IF EXISTS before CREATE.
"""


def main_module(owns, key):
    for p in owns:
        if Path(p).name.startswith(key):
            return p
    return owns[0] if owns else f"{key}.py"


def build_contract(ctx, task):
    spec, params = ctx.spec, ctx.spec["params"]
    module, owned, inputs = task["module"], task["owned"], task["inputs"]
    head = (f"TASK {task['id']} (module {module}). You are agent {task['agent']}.\n"
            f"Stdlib only, Python >= 3.10. Files you may write (exact paths/globs): {owned}\n"
            f"Read-only input files already in your directory: {inputs or '(none)'} plus spec.json and params.json "
            f"(params.json = {json.dumps(params)}).\n")
    owns_by_module = {m["id"]: m.get("owns", []) for m in spec["modules"]}
    if module == "collect":
        mod = main_module(owns_by_module.get("collect", []), "collect")
        pages = "\n".join(spec["source"]["pages"])
        return head + METHOD + f"""
YOUR JOB (collector):
1. Call http_fetch once for EACH of these pages to inspect the structure (only these addresses; nothing else):
{pages}
2. Write {mod} with a function collect(source: dict) -> list[dict] that fetches every URL in source["pages"] with
   urllib.request (use an opener with ProxyHandler({{}}) so no proxy is used) and parses it with html.parser:
   the site name is in <h1 data-site="...">; every <tr data-device=".." data-day=".."> inside table#readings is one record,
   its value is the text of <td class="value"> as float. Return ALL records in page order, no filtering.
   Add a CLI: python {mod} --spec spec.json --out data/records.jsonl (reads spec["source"], writes one JSON object per line).
3. Create any empty package files listed in your allowed files (for example __init__.py).
4. Run the CLI with run_bash, then verify the line count of data/records.jsonl with a small helper script.
Conclude with the number of records and sites collected."""
    if module == "analyze":
        a = main_module(owns_by_module.get("analyze", []), "analyze")
        s = main_module(owns_by_module.get("store", []), "store")
        return head + METHOD + f"""
YOUR JOB (data coder): input data/records.jsonl is already present.
1. Write {a} with analyze(records: list[dict], params: dict) -> dict implementing the summary methodology above exactly.
   CLI: python {a} --records data/records.jsonl --params params.json --out data/summary.json
2. Write {s} with store(records, summary, db_path) creating app.db as specified (idempotent).
   CLI: python {s} --records data/records.jsonl --summary data/summary.json --db data/app.db
3. Run both CLIs with run_bash (analyze first). Then verify with a helper script: row counts in app.db, summary keys,
   and that by_site / top_devices ordering follows the rules.
Create any package __init__.py files listed in your allowed files. Conclude with record_count, kept_count and total."""
    if module == "api":
        app = main_module(owns_by_module.get("api", []), "app")
        return head + f"""
YOUR JOB (backend coder): write {app} (and any package files you own): a standard-library HTTP server (http.server).
Data files already present: data/summary.json, data/records.jsonl, data/app.db.
 - ROOT = Path(__file__).resolve().parents[1]; read data from ROOT/data, serve static files from ROOT/site; never use cwd.
 - Usage: python server/app.py --port P. With --port 0 the OS assigns the port; print EXACTLY ONE line to stdout:
   LISTENING <actual port> (flush it); send every log message to stderr.
 - GET /api/health -> {{"ok": true}}
 - GET /api/summary -> {{"summary": <content of data/summary.json>}}
 - GET /api/records?site=X&limit=N -> {{"records": [...]}} from data/records.jsonl (ALL records, unfiltered), filtered by site
   when given, sorted ascending by (device, day), truncated to limit when given (ignore an invalid limit)
 - GET / -> site/index.html; GET /app.js, /style.css, /data.json -> the matching files in site/ (correct Content-Type)
 - Expose importable functions for tests: load_summary(root=ROOT) -> dict, load_records(root=ROOT, site=None, limit=None) -> list
Verify with a helper script that imports the module, calls the functions, starts the server with --port 0 as a subprocess,
requests /api/health, /api/summary and /api/records?site=<a real site>&limit=2, then terminates the server.
Conclude with what you verified."""
    if module == "ui":
        return head + f"""
YOUR JOB (frontend coder): produce the site files you own (index.html, app.js, style.css, data.json).
 - index.html: valid HTML5 referencing app.js and style.css, containing <div id="summary"> and <div id="chart">.
 - app.js (at least 400 bytes of real code): fetch("data.json"), then render the summary (unit, record_count, kept_count,
   total, mean, max, min), a by_site table and top_devices list into #summary, and a bar chart of by_site totals (inline SVG
   or divs) into #chart. NEVER hardcode numbers or site names in HTML or JS.
 - style.css (at least 200 bytes): readable layout and chart styling.
 - data.json must be GENERATED, not typed: write a helper script (e.g. site/_build_data.py) that reads data/summary.json and
   data/records.jsonl and writes site/data.json as {{"summary": <summary>, "records": [<all records>]}}; run it with run_bash.
Conclude with what you produced."""
    if module == "qa":
        t1 = [p for p in owns_by_module.get("qa", [])] or ["tests/test_pipeline.py", "tests/test_api.py"]
        a = main_module(owns_by_module.get("analyze", []), "analyze")
        s = main_module(owns_by_module.get("store", []), "store")
        app = main_module(owns_by_module.get("api", []), "app")
        return head + METHOD + f"""
YOUR JOB (tester): write {t1} using unittest (at least 5 test methods in total, all deterministic and offline).
Under test (already present, do not modify): {a} -> analyze(records, params); {s} -> store(records, summary, db_path);
{app} -> load_summary(root), load_records(root, site=None, limit=None).
Start each test file with sys.path.insert(0, str(Path(__file__).resolve().parents[1])) so imports work from any cwd.
Build SMALL synthetic record lists inside the tests (values chosen so ties and the threshold matter) and cover at least:
 - threshold filtering (value >= min_value, boundary value kept, record_count counts all rows)
 - by_site ordering (total descending, ties by site ascending)
 - top_devices truncation to top_k with tie ordering
 - SQLite round-trip using a temporary file (idempotent: call store twice)
 - API data functions load_summary / load_records (site filter, ordering by device/day, limit) against a temporary root
   directory that you create with data/summary.json and data/records.jsonl.
Run: python -m unittest discover -s tests -q (from your directory). It must pass. Conclude with the number of tests."""
    if module == "deploy":
        return head + """
YOUR JOB (deployer): write deploy/README.md containing: (1) the start command `python server/app.py --port 8000`,
(2) the health check `curl http://127.0.0.1:8000/api/health` returning {"ok": true}, (3) how to run the tests
`python -m unittest discover -s tests -q`, (4) the data layout (data/records.jsonl, summary.json, app.db, site/data.json).
Keep it accurate and short. Conclude in one line."""
    return head + "Do the work described by the module id."


CHECKLISTS = {
    "collect": ["collect(source) exists, uses urllib with no proxy and html.parser (no bs4/requests)",
                "site comes from h1[data-site]; every tr[data-device][data-day] row is one record; value is float",
                "NO filtering or rounding; records.jsonl has one JSON object per record with exactly site/device/day/value",
                "no hardcoded site names, urls or counts; URLs come from the spec"],
    "analyze": ["filter is value >= min_value (not >); record_count counts ALL rows",
                "total sums raw values then rounds once; mean = round(total/kept_count) with the rounded total",
                "by_site sorted by (-total, site); sites without kept rows omitted; fields site,total,mean,device_count,kept_count",
                "top_devices sorted by (-total, site, device), truncated to top_k; fields site,device,total,mean",
                "max/min tie-break by smallest (site, device, day); handles zero kept records without crashing",
                "no hardcoded thresholds/precision/top_k/site names (params come from params.json)",
                "app.db: records PK(site,device,day) with ALL rows, summary table with total/mean/record_count, DROP before CREATE",
                "output files exist (data/summary.json, data/app.db) and the verification run output looks right"],
    "api": ["--port 0 prints exactly one line `LISTENING <port>` to stdout, logs go to stderr",
            "ROOT derived from __file__ (not cwd); data read from data/, static from site/",
            "/api/health, /api/summary ({summary: ...}), /api/records (site filter, sort by device/day, limit) implemented",
            "GET / serves index.html; /app.js /style.css /data.json served; load_summary and load_records exist",
            "does not return constants: responses come from the data files"],
    "ui": ["index.html references app.js and style.css and has #summary and #chart",
           "app.js fetches data.json and renders summary, by_site table, top devices and a chart; no hardcoded data",
           "style.css is real styling; data.json has summary and records and was generated from the data files"],
    "qa": ["at least 5 unittest cases; deterministic and offline; sys.path set from __file__",
           "covers threshold filtering, by_site ordering, top_devices truncation, SQLite round-trip, API data functions",
           "assertions are meaningful (check values/order, not just that code runs); the run output shows all green"],
    "deploy": ["deploy/README.md has the start command with server/app.py, the /api/health check and the test command"],
}


def review_prompt(task, bundle, history_tail):
    items = CHECKLISTS.get(task["module"], ["deliverables exist and are consistent with the contract"])
    return (f"Review task {task['id']} (agent {task['agent']}). Produced files and evidence follow.\n"
            f"CHECKLIST - verify EACH item explicitly against the code and the run output; list a defect for every item "
            f"that is not satisfied or cannot be confirmed:\n- " + "\n- ".join(items) +
            "\n\nOUTPUT: exactly one JSON object and nothing else:\n"
            '{"verdict": "approved" | "changes_requested", "defects": [{"file": "<path>", "why": "<concrete problem>"}]}\n'
            "Use approved only when defects is empty. Be strict and concrete; do not invent problems that are not in the files.\n\n"
            f"=== FILES ===\n{bundle}\n\n=== LAST TOOL OUTPUTS OF THE AGENT ===\n{history_tail}\n")


# ------------------------------------------------------------------ engine
class Engine:
    def __init__(self, opts):
        self.o = opts
        self.run_dir = Path.cwd()
        self.spec = M.load_json_file(opts["spec"])
        self.roles = {r["name"]: r for r in M.load_roles()}
        pol = opts.get("policy")
        self.policy = M.load_json_file(pol) if pol and Path(pol).is_file() else DEFAULT_POLICY
        self.workspace = (self.run_dir / opts["workspace"]).resolve()
        self.steps = 0
        self.tokens = 0
        self.lock = threading.Lock()
        self.abort = None
        self.hist = {}
        self.approve_lock = threading.Lock()
        self.last_outputs = {}

    # -- budget / model
    def chat(self, agent, messages):
        with self.lock:
            if self.abort:
                raise BudgetExceeded(self.abort)
            if self.steps >= self.o["max_steps"]:
                self.abort = "step_limit"
                raise BudgetExceeded("step_limit")
            self.steps += 1
        text, tok = self.provider.chat(messages)
        with self.lock:
            self.tokens += tok
            mt = self.o.get("max_tokens")
            if mt and self.tokens >= mt:
                self.abort = "token_limit"
        return text

    def approve(self, kind, detail):
        if os.environ.get("MAGENT_APPROVE") == "auto" or not sys.stdin or not sys.stdin.isatty():
            return True
        with self.approve_lock:
            ans = input(f"[approve] {kind}: {detail}  (y/n) ").strip().lower()
        return ans in ("y", "yes")

    # -- planning
    def plan(self):
        spec_modules = [m["id"] for m in self.spec["modules"]]
        by_module = {}
        for name, r in self.roles.items():
            for m in r.get("modules", []):
                by_module.setdefault(m, name)
        fallback, seen = [], set()
        for m in spec_modules + ["deploy"]:
            agent = by_module.get(m)
            if not agent or agent in seen:
                continue
            seen.add(agent)
            fallback.append({"id": m, "module": m, "agent": agent})
        tasks, source = fallback, "fallback"
        sys_msg = "AGENT: planner\n" + self.roles["planner"]["systemPrompt"] + "\nOutput exactly one JSON object: " \
            '{"tasks":[{"id":"<module id>","module":"<module id>","agent":"<executor>"}]}'
        execs = {n: r.get("modules", []) for n, r in self.roles.items() if r.get("modules")}
        user = (f"Spec modules: {json.dumps(self.spec['modules'])}\nExecutors and the modules each can own: "
                f"{json.dumps(execs)}\nGroup modules owned by the same executor into ONE task (id = first module id). "
                "Also add a final task with module 'deploy' for the deployer.")
        try:
            text = self.chat("planner", [{"role": "system", "content": sys_msg}, {"role": "user", "content": user}])
            data = extract_json(text) or {}
            cand = data.get("tasks") or []
            valid = (isinstance(cand, list) and cand and all(
                isinstance(t, dict) and {"id", "module", "agent"} <= set(t) and t["agent"] in execs
                and t["module"] in execs[t["agent"]] for t in cand))
            cov = {t["agent"] for t in cand} if valid else set()
            if valid and cov == {t["agent"] for t in fallback} and len({t["id"] for t in cand}) == len(cand):
                tasks, source = [{"id": str(t["id"]), "module": t["module"], "agent": t["agent"]} for t in cand], "model"
        except BudgetExceeded:
            raise
        self.plan_source = source
        return tasks

    def build_tasks(self, plan):
        owns_all = {m["id"]: m.get("owns", []) for m in self.spec["modules"]}
        tasks = []
        for t in plan:
            role = self.roles[t["agent"]]
            owned = []
            for m in role.get("modules", []):
                owned += owns_all.get(m, [])
            if t["module"] == "collect":
                owned.append("data/records.jsonl")
            if t["module"] == "analyze":
                owned += ["data/summary.json", "data/app.db"]
            if t["module"] == "deploy":
                owned.append("deploy/README.md")
            deps_modules = role.get("dependsOn", [])
            task = dict(t, owned=sorted(set(owned)), deps=[], written=[], dep_modules=deps_modules, status="pending",
                        reviews=0, fixes=0)
            tasks.append(task)
        mod_to_task = {}
        for t in tasks:
            for m in self.roles[t["agent"]].get("modules", []):
                mod_to_task[m] = t["id"]
            mod_to_task[t["module"]] = t["id"]
        for t in tasks:
            t["deps"] = sorted({mod_to_task[m] for m in t["dep_modules"] if m in mod_to_task and mod_to_task[m] != t["id"]})
        return tasks

    # -- agent loop
    def agent_loop(self, task, user_msg):
        agent = task["agent"]
        cfg = self.roles[agent]
        tools_doc = []
        for tool in M.TOOLS:
            if tool["name"] in cfg["allowedTools"] and tool["name"] not in ("task", "merge_files"):
                tools_doc.append(f"- {tool['name']}: {tool['description']} args={json.dumps(tool['parameters']['properties'])}")
        system = (f"AGENT: {agent}\nROLE: {cfg['role']}\n{cfg['systemPrompt']}\n{PROTOCOL}\nTOOLS:\n" + "\n".join(tools_doc))
        hist = self.hist.setdefault(agent, [{"role": "system", "content": system}])
        hist.append({"role": "user", "content": user_msg})
        tools = AgentTools(self, agent, cfg, f"agents/{agent}", task)
        for _ in range(cfg["maxSteps"]):
            text = self.chat(agent, hist)
            hist.append({"role": "assistant", "content": text})
            calls, final, errs = parse_reply(text)
            if calls:
                results = list(errs)
                for k, (name, args) in enumerate(calls[:MAX_TOOLS_PER_TURN], 1):
                    ok, out = tools.run(name, args)
                    results.append(f"[RESULT {k}] {name} ok={ok}\n{out}")
                    self.last_outputs.setdefault(agent, []).append(f"$ {name} {json.dumps(args)[:160]} -> ok={ok}\n{clip(out, 700)}")
                hist.append({"role": "user", "content": "\n\n".join(results)})
                continue
            if final is not None:
                return True, final
            hist.append({"role": "user", "content": "Format error: " + ("; ".join(errs) or "no @tool or @final line found")
                         + ". Reply using the TOOL PROTOCOL."})
        return False, "agent exhausted its maxSteps without a conclusion"

    def prepare_workdir(self, task):
        wd = self.run_dir / "agents" / task["agent"]
        wd.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.run_dir / "spec.json", wd / "spec.json") if (self.run_dir / "spec.json").is_file() else \
            (wd / "spec.json").write_text(json.dumps(self.spec), encoding="utf-8")
        (wd / "params.json").write_text(json.dumps(self.spec["params"]), encoding="utf-8")
        inputs = []
        upstream, stack = [], list(task["deps"])
        while stack:  # inputs come from every upstream task, not only direct dependencies
            d = stack.pop()
            if d not in upstream:
                upstream.append(d)
                stack.extend(self.by_id[d]["deps"])
        for dep_id in upstream:
            dep = self.by_id[dep_id]
            for rel in dep["deliver"]:
                src = self.run_dir / "agents" / dep["agent"] / rel
                dst = wd / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                inputs.append(rel)
        task["inputs"] = sorted(set(inputs))

    def deliverables(self, task):
        return sorted(f for f in task["written"] if M.glob_match(f, task["owned"]) and not M.glob_match(f, SCRATCH_GLOBS)
                      and (self.run_dir / "agents" / task["agent"] / f).is_file())

    # -- review
    def review(self, task):
        t = self.trace
        wd_rel = f"agents/{task['agent']}"
        wd = self.run_dir / wd_rel
        files = self.deliverables(task)
        t.emit("reviewer", "reviewer", "tool", tool="glob", workdir=wd_rel, ok=True, pattern="**/*")
        parts = []
        for f in files:
            p = wd / f
            if f.endswith(".db"):
                try:
                    c = sqlite3.connect(str(p))
                    tabs = [r[0] for r in c.execute("select name from sqlite_master where type='table'")]
                    info = {tb: c.execute(f"select count(*) from {tb}").fetchone()[0] for tb in tabs}
                    c.close()
                except Exception as exc:
                    info = f"unreadable: {exc}"
                parts.append(f"--- {f} (sqlite) table row counts: {info}")
            else:
                text = p.read_text(encoding="utf-8", errors="replace")
                if f.endswith(".jsonl"):
                    ls = text.splitlines()
                    text = f"({len(ls)} lines) first 5:\n" + "\n".join(ls[:5])
                parts.append(f"--- {f}\n{clip(text, 7000)}")
            t.emit("reviewer", "reviewer", "tool", tool="read_file", workdir=wd_rel, ok=True, path=f)
        tail = "\n".join(self.last_outputs.get(task["agent"], [])[-8:]) or "(none)"
        msgs = [{"role": "system", "content": "AGENT: reviewer\n" + self.roles["reviewer"]["systemPrompt"] +
                 "\nYou cannot run code; judge from the files and the recorded outputs. Output exactly one JSON object."},
                {"role": "user", "content": review_prompt(task, "\n\n".join(parts) or "(no files produced)", tail)}]
        for attempt in range(2):
            text = self.chat("reviewer", msgs)
            data = extract_json(text)
            if isinstance(data, dict) and data.get("verdict") in M.VERDICTS:
                defects = [d for d in (data.get("defects") or []) if isinstance(d, dict) and "why" in d]
                defects = [{"file": str(d.get("file", "-")), "why": str(d["why"])} for d in defects]
                verdict = data["verdict"]
                if verdict == "approved":
                    defects = []
                elif not defects:
                    defects = [{"file": "-", "why": "reviewer requested changes without details"}]
                return verdict, defects
            msgs.append({"role": "assistant", "content": text})
            msgs.append({"role": "user", "content": "Invalid output. Reply with exactly one JSON object with verdict and defects."})
        return "changes_requested", [{"file": "-", "why": "reviewer output was not valid JSON"}]

    # -- one task: execute -> review -> fix loop
    def run_task(self, task):
        t, agent, role = self.trace, task["agent"], self.roles[task["agent"]]["role"]
        try:
            self.prepare_workdir(task)
            ok, final = self.agent_loop(task, build_contract(self, task))
            t.emit(agent, role, "result", task=task["id"], ok=ok, **({} if ok else {"error": final}),
                   summary=clip(final, 300))
            if not ok:
                task["status"] = "failed"
                return
            rounds = self.o.get("max_review_rounds", 3)
            for rnd in range(1, rounds + 1):
                verdict, defects = self.review(task)
                task["reviews"] += 1
                t.emit("reviewer", "reviewer", "review", task=task["id"], verdict=verdict, defects=defects, round=rnd)
                if verdict == "approved":
                    task["status"] = "done"
                    task["deliver"] = self.deliverables(task)
                    return
                if rnd == rounds:
                    task["status"] = "unresolved"
                    return
                t.emit("orchestrator", "orchestrator", "handoff", to=agent, task=task["id"],
                       workdir=f"agents/{agent}", rework=True, round=rnd, child_session="sess-" + agent)
                msg = ("REVIEW DEFECTS - fix every item below in your own files, re-run your verification, then send @final.\n" +
                       "\n".join(f"- {d['file']}: {d['why']}" for d in defects))
                ok, final = self.agent_loop(task, msg)
                task["fixes"] += 1
                t.emit(agent, role, "fix", task=task["id"], defects_fixed=len(defects))
                t.emit(agent, role, "result", task=task["id"], ok=ok, **({} if ok else {"error": final}),
                       summary=clip(final, 300))
                if not ok:
                    task["status"] = "failed"
                    return
        except BudgetExceeded:
            task["status"] = "budget"
        except Exception as exc:  # a failing sub-task must not sink the run
            task["status"] = "failed"
            t.emit(agent, role, "result", task=task["id"], ok=False, error=f"{type(exc).__name__}: {exc}"[:300])

    # -- merge (integrator = single write gateway)
    def merge_all(self, tasks):
        t = self.trace
        owner, merged = {}, set()
        for task in tasks:
            if task["status"] != "done":
                continue
            copied, conflicts = [], []
            for rel in task["deliver"]:
                if rel in owner and owner[rel] != task["id"]:
                    conflicts.append({"file": rel, "kept": owner[rel], "dropped": task["id"]})
                    continue
                src = self.run_dir / "agents" / task["agent"] / rel
                dst = self.workspace / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                owner[rel] = task["id"]
                copied.append(rel)
            fields = {"conflicts": conflicts} if conflicts else {}
            t.emit("integrator", "integrator", "merge", from_workdir=f"agents/{task['agent']}", files=copied, **fields)

    # -- deploy + self-test
    def deploy_and_test(self):
        t, ws = self.trace, self.workspace
        proc, port, healthy = None, None, False
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        try:
            proc = subprocess.Popen([sys.executable, "server/app.py", "--port", "0"], cwd=str(ws), env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            deadline = time.time() + 15
            while time.time() < deadline and port is None:
                line = proc.stdout.readline()
                if not line:
                    break
                m = re.match(r"LISTENING (\d+)", line.strip())
                if m:
                    port = int(m.group(1))
            if port:
                import http.client
                for _ in range(40):
                    try:
                        c = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                        c.request("GET", "/api/health")
                        r = c.getresponse()
                        body = r.read().decode("utf-8", "replace")
                        c.close()
                        if r.status == 200 and json.loads(body).get("ok") is True:
                            healthy = True
                            break
                    except Exception:
                        time.sleep(0.25)
        except Exception:
            pass
        finally:
            if proc is not None:
                try:
                    proc.terminate()
                    proc.wait(timeout=8)
                except Exception:
                    proc.kill()
        t.emit("deployer", "deployer", "deploy", command=f"python server/app.py --port {port or 0}", port=port or 0, health=healthy)
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"], cwd=str(ws), env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        text = (r.stderr or "") + (r.stdout or "")
        m = re.search(r"Ran (\d+) tests?", text)
        total = int(m.group(1)) if m else 0
        bad = sum(int(x) for x in re.findall(r"(?:failures|errors)=(\d+)", text))
        ok = r.returncode == 0 and total >= 1
        t.emit("tester", "tester", "test", suite="unittest", passed=max(total - bad, 0), failed=bad if total else 1, ok=ok)
        return healthy, ok, clip(text, 600)

    # -- main
    def execute(self):
        o = self.o
        random.seed(o.get("seed"))
        run_id = "run-" + time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-" + os.urandom(3).hex()
        for d in (self.run_dir / "agents", self.workspace):
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
        self.workspace.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "agents").mkdir(exist_ok=True)
        if not (self.run_dir / "spec.json").is_file():
            shutil.copy2(o["spec"], self.run_dir / "spec.json")
        cfg = M.load_json_file(HERE / "config.json")
        self.provider = Provider(cfg)
        self.trace = Trace((self.run_dir / o["trace"]).resolve() if not os.path.isabs(o["trace"]) else Path(o["trace"]), run_id)
        t = self.trace
        t.emit("orchestrator", "orchestrator", "run_start", spec=str(o["spec"]),
               provider={"name": self.provider.name, "model": self.provider.model}, seed=o.get("seed"),
               max_steps=o["max_steps"], parallel=o["parallel"])
        status, error = "ok", None
        tasks = []
        try:
            plan = self.plan()
            tasks = self.build_tasks(plan)
            self.by_id = {x["id"]: x for x in tasks}
            t.emit("planner", "planner", "plan", planner=self.plan_source,
                   tasks=[{"id": x["id"], "module": x["module"], "agent": x["agent"], "deps": x["deps"]} for x in tasks])
            for x in tasks:  # all handoffs go out before any agent starts working (parallel dispatch)
                t.emit("orchestrator", "orchestrator", "handoff", to=x["agent"], task=x["id"],
                       workdir=f"agents/{x['agent']}", child_session="sess-" + x["agent"], deps=x["deps"],
                       files=x["owned"])
            self.schedule(tasks)
            if self.abort:
                status = self.abort
            elif any(x["status"] == "unresolved" for x in tasks):
                status = "unresolved"
            elif any(x["status"] != "done" for x in tasks):
                status, error = "error", "tasks failed: " + ", ".join(f"{x['id']}={x['status']}" for x in tasks if x["status"] != "done")
            else:
                self.merge_all(tasks)
                healthy, tests_ok, tail = self.deploy_and_test()
                if not (healthy and tests_ok):
                    status, error = "error", f"deploy healthy={healthy}, tests ok={tests_ok}\n{tail}"
        except BudgetExceeded as exc:
            status = exc.status
        except ProviderError as exc:
            status, error = "error", str(exc)
        except Exception as exc:
            status, error = "error", f"{type(exc).__name__}: {exc}"
        t.emit("orchestrator", "orchestrator", "run_end", status=status, steps=self.steps, tokens=self.tokens,
               **({"error": error} if error else {}))
        t.close()
        self.write_report(run_id, status, tasks)
        errs = M.trace_errors(M.read_jsonl(t.path))
        if errs:
            sys.stderr.write("trace self-check problems:\n  " + "\n  ".join(errs[:10]) + "\n")
        if error:
            sys.stderr.write(f"run ended with status {status}: {error}\n")
        return {"ok": M.EXIT_OK, "step_limit": M.EXIT_GUARD, "token_limit": M.EXIT_GUARD,
                "unresolved": M.EXIT_UNRESOLVED}.get(status, M.EXIT_USAGE)

    def schedule(self, tasks):
        done_ids, running = set(), {}
        pool = ThreadPoolExecutor(max_workers=max(1, self.o["parallel"]))
        try:
            while True:
                for x in tasks:
                    if x["status"] == "pending" and x["id"] not in running and not self.abort:
                        if any(self.by_id[d]["status"] in ("failed", "unresolved", "budget", "skipped") for d in x["deps"]):
                            x["status"] = "skipped"
                            continue
                        if all(self.by_id[d]["status"] == "done" for d in x["deps"]):
                            running[x["id"]] = pool.submit(self.run_task, x)
                if not running:
                    break
                finished, _ = wait(list(running.values()), return_when=FIRST_COMPLETED)
                for tid in [k for k, f in running.items() if f in finished]:
                    running.pop(tid)
                if self.abort and not running:
                    break
        finally:
            pool.shutdown(wait=True)

    def write_report(self, run_id, status, tasks):
        rows = M.read_jsonl(self.trace.path)
        rep = M.replay_rows(rows)
        rep.update({"run_id": run_id, "status": status, "steps": self.steps, "tokens": self.tokens,
                    "model": self.provider.model, "plan_source": getattr(self, "plan_source", None)})
        rep["tasks"] = [{"id": x["id"], "agent": x["agent"], "reviews": x["reviews"], "fixes": x["fixes"],
                         "status": x["status"]} for x in tasks] or rep["tasks"]
        (self.run_dir / "run_report.json").write_text(json.dumps(rep, indent=2), encoding="utf-8")


def run_crew(opts):
    try:
        eng = Engine(opts)
        return eng.execute()
    except ProviderError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return M.EXIT_USAGE
