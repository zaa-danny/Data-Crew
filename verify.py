#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验三 · 多 Agent 数据应用工厂（DataCrew）自动验收

用法：
    python verify.py <入口.py>                    # 离线契约检查（不调模型，秒级）
    python verify.py <入口.py> --live             # 真实运行（采集→分析→入库→站点→API→部署→自测）
    python verify.py <入口.py> --live --seed 7    # 指定 seed（换一套数据源与参数，防硬编码）
    python verify.py <入口.py> --both             # 离线 + 真实
    python verify.py <入口.py> --live --seeds 3   # 教师抽查：连跑 3 套规格

设计原则：
    1) 只按对外契约验收：CLI 子命令、trace.jsonl、policy 判定、workspace 产物、真实 HTTP 服务。
    2) 数据源由本脚本现场生成（本地 HTTP 服务），标准答案由本脚本独立计算，不 import 学生代码。
    3) 离线检查完全确定性；真实运行用随机规格（站点数 / 设备数 / 天数 / 阈值 / top_k / 精度）防硬编码。

退出码：0 = 全绿；1 = 有未通过项。
"""

from __future__ import annotations

import argparse
import functools
import http.client
import json
import os
import random
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"
PY = sys.executable

ROLES = {"orchestrator", "planner", "collector", "coder", "reviewer", "integrator", "tester", "deployer"}
EVENTS = {"run_start", "plan", "handoff", "tool", "result", "review", "fix", "merge",
          "deploy", "test", "run_end"}
VERDICTS = {"approved", "changes_requested"}
STATUSES = {"ok", "step_limit", "token_limit", "unresolved", "error"}
WRITE_TOOLS = {"write_file", "apply_patch"}
REQUIRED_TOOLS = {"http_fetch", "write_file", "run_bash"}

SITE_POOL = ["north", "south", "east", "west", "center"]


# ---------------------------------------------------------------- 基础工具

def sh(cmd, cwd, timeout=120, env=None):
    e = os.environ.copy()
    if env:
        e.update(env)
    try:
        return subprocess.run(
            [str(c) for c in cmd], cwd=str(cwd), capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout, env=e,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(cmd, 124, exc.stdout or "", f"TIMEOUT after {timeout}s")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_jsonl(path):
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def http_get(port, path, timeout=6):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, resp.read().decode("utf-8", "replace")
    finally:
        conn.close()


class Board:
    def __init__(self, title):
        self.title = title
        self.checks = []

    def section(self):
        print(f"\n═══ {self.title} ═══")

    def ok(self, cond, msg):
        self.checks.append(bool(cond))
        print(f"  {'✅' if cond else '❌'} {msg}")
        return bool(cond)

    @property
    def passed(self):
        return sum(self.checks)

    @property
    def total(self):
        return len(self.checks)


# ---------------------------------------------------------------- 数据源生成（标准答案的唯一来源）

def make_dataset(seed: int) -> dict:
    rnd = random.Random(seed * 7919 + 13)
    sites = rnd.sample(SITE_POOL, rnd.randint(3, 4))
    params = {
        "unit": rnd.choice(["kWh", "m3", "count"]),
        "min_value": rnd.choice([0.0, 5.0, 10.0]),
        "top_k": rnd.randint(2, 4),
        "round_digits": rnd.choice([1, 2]),
    }
    records = []
    for site in sites:
        n_dev = rnd.randint(3, 4)
        n_day = rnd.randint(4, 6)
        for d in range(n_dev):
            device = f"{site}-d{d + 1:02d}"
            for k in range(n_day):
                value = round(rnd.uniform(0.5, 99.9), 1)
                records.append({
                    "site": site,
                    "device": device,
                    "day": f"2026-03-{k + 1:02d}",
                    "value": value,
                })
    return {"seed": seed, "sites": sites, "params": params, "records": records}


def expected_summary(ds: dict) -> dict:
    p = ds["params"]
    rd = p["round_digits"]
    records = ds["records"]
    kept = [r for r in records if r["value"] >= p["min_value"]]

    total = round(sum(r["value"] for r in kept), rd)
    mean = round(total / len(kept), rd) if kept else 0.0

    by_site = []
    for site in ds["sites"]:
        mine = [r for r in kept if r["site"] == site]
        if not mine:
            continue
        s_total = round(sum(r["value"] for r in mine), rd)
        by_site.append({
            "site": site,
            "total": s_total,
            "mean": round(s_total / len(mine), rd),
            "device_count": len({r["device"] for r in mine}),
            "kept_count": len(mine),
        })
    by_site.sort(key=lambda x: (-x["total"], x["site"]))

    by_device = {}
    for r in kept:
        by_device.setdefault((r["site"], r["device"]), []).append(r["value"])
    top = [
        {
            "site": k[0],
            "device": k[1],
            "total": round(sum(v), rd),
            "mean": round(sum(v) / len(v), rd),
        }
        for k, v in by_device.items()
    ]
    top.sort(key=lambda x: (-x["total"], x["site"], x["device"]))

    picked = {
        "max": sorted(kept, key=lambda r: (-r["value"], r["site"], r["device"], r["day"]))[0],
        "min": sorted(kept, key=lambda r: (r["value"], r["site"], r["device"], r["day"]))[0],
    }

    return {
        "unit": p["unit"],
        "record_count": len(records),
        "kept_count": len(kept),
        "site_count": len({r["site"] for r in kept}),
        "device_count": len(by_device),
        "min_value": p["min_value"],
        "round_digits": rd,
        "top_k": p["top_k"],
        "total": total,
        "mean": mean,
        "max": picked["max"],
        "min": picked["min"],
        "by_site": by_site,
        "top_devices": top[: p["top_k"]],
    }


def html_page(site: str, rows) -> str:
    trs = "\n".join(
        '      <tr data-device="{0}" data-day="{1}">'
        '<td class="device">{0}</td><td class="day">{1}</td>'
        '<td class="value">{2:.1f}</td></tr>'.format(r["device"], r["day"], r["value"])
        for r in rows
    )
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head><meta charset=\"utf-8\">"
        f"<title>Site {site}</title></head>\n<body>\n"
        f"  <h1 class=\"site\" data-site=\"{site}\">{site}</h1>\n"
        "  <table id=\"readings\">\n"
        "    <thead><tr><th>device</th><th>day</th><th>value</th></tr></thead>\n"
        f"    <tbody>\n{trs}\n    </tbody>\n  </table>\n</body>\n</html>\n"
    )


def write_source(ds: dict, root: Path):
    root.mkdir(parents=True, exist_ok=True)
    links = "\n".join(
        f'  <li><a href="sites/{s}.html">{s}</a></li>' for s in ds["sites"]
    )
    (root / "index.html").write_text(
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<title>Sensor index</title></head>\n<body>\n  <h1>Sensor index</h1>\n"
        f"  <ul>\n{links}\n  </ul>\n</body>\n</html>\n",
        encoding="utf-8",
    )
    (root / "sites").mkdir(exist_ok=True)
    for site in ds["sites"]:
        rows = [r for r in ds["records"] if r["site"] == site]
        (root / "sites" / f"{site}.html").write_text(html_page(site, rows), encoding="utf-8")


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def serve_dir(root: Path):
    handler = functools.partial(_QuietHandler, directory=str(root))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def make_spec(ds: dict, base: str) -> dict:
    return {
        "task_id": f"dataapp-{ds['seed']:04d}",
        "goal": "采集站点数据 → 分析 → 入库 → 可视化站点 → 后端 API → 部署 → 自测全部通过",
        "source": {
            "kind": "http",
            "index": f"{base}/index.html",
            "pages": [f"{base}/sites/{s}.html" for s in ds["sites"]],
        },
        "params": ds["params"],
        "modules": [
            {"id": "collect", "owns": ["pipeline/collect.py", "pipeline/__init__.py"],
             "interface": "collect(source: dict) -> list[record]"},
            {"id": "analyze", "owns": ["pipeline/analyze.py"],
             "interface": "analyze(records, params) -> summary"},
            {"id": "store", "owns": ["pipeline/store.py"],
             "interface": "store(records, summary, db_path)"},
            {"id": "api", "owns": ["server/app.py", "server/__init__.py"],
             "interface": "GET /api/health | /api/summary | /api/records?site=&limit="},
            {"id": "ui", "owns": ["site/index.html", "site/app.js", "site/style.css", "site/data.json"]},
            {"id": "qa", "owns": ["tests/test_pipeline.py", "tests/test_api.py"]},
        ],
        "acceptance": [
            "python -m unittest discover -s tests -q",
            "python server/app.py --port 0",
        ],
        "constraints": {"stdlib_only": True, "python": ">=3.10", "network": "仅 source 中的地址"},
    }


# ---------------------------------------------------------------- 数值与结构比对

def diff_obj(got, want, path="", tol=1e-6, out=None):
    if out is None:
        out = []
    if isinstance(want, dict):
        if not isinstance(got, dict):
            out.append(f"{path or '.'}: 期望对象，实际 {type(got).__name__}")
            return out
        for key, val in want.items():
            if key not in got:
                out.append(f"{path}.{key}: 缺少字段")
            else:
                diff_obj(got[key], val, f"{path}.{key}", tol, out)
        return out
    if isinstance(want, list):
        if not isinstance(got, list):
            out.append(f"{path or '.'}: 期望数组，实际 {type(got).__name__}")
            return out
        if len(got) != len(want):
            out.append(f"{path or '.'}: 长度 {len(got)} ≠ 期望 {len(want)}")
        for i, val in enumerate(want[: len(got)]):
            diff_obj(got[i], val, f"{path}[{i}]", tol, out)
        return out
    if isinstance(want, (int, float)) and not isinstance(want, bool):
        if not isinstance(got, (int, float)) or isinstance(got, bool):
            out.append(f"{path or '.'}: 期望数值，实际 {got!r}")
        elif abs(got - want) > tol:
            out.append(f"{path or '.'}: {got} ≠ 期望 {want}")
        return out
    if got != want:
        out.append(f"{path or '.'}: {got!r} ≠ 期望 {want!r}")
    return out


# ---------------------------------------------------------------- trace 校验（离线 / 在线共用）

def trace_errors(rows):
    errs = []
    if not rows:
        return ["trace 为空"]
    prev_seq = 0
    run_ids = set()
    plan_tasks = {}
    handoffs = []
    writes = {}
    merges = []
    reviews = {}
    fixes = set()
    ended = None

    for i, r in enumerate(rows, 1):
        where = f"第 {i} 行"
        for key in ("seq", "ts", "run_id", "session", "agent", "role", "event"):
            if key not in r:
                errs.append(f"{where}: 缺少字段 {key}")
        if "seq" in r:
            if not isinstance(r["seq"], int) or r["seq"] <= prev_seq:
                errs.append(f"{where}: seq 必须严格递增（{prev_seq} → {r.get('seq')}）")
            else:
                prev_seq = r["seq"]
        if "ts" in r and not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$", str(r["ts"])):
            errs.append(f"{where}: ts 不是 ISO8601 UTC（{r.get('ts')}）")
        if "role" in r and r["role"] not in ROLES:
            errs.append(f"{where}: 非法 role {r['role']}")
        if "event" in r and r["event"] not in EVENTS:
            errs.append(f"{where}: 非法 event {r['event']}")
        if "run_id" in r:
            run_ids.add(r["run_id"])

        ev = r.get("event")
        if ev == "plan":
            for t in r.get("tasks") or []:
                if not {"id", "module", "agent"} <= set(t):
                    errs.append(f"{where}: plan 任务缺少 id/module/agent")
                else:
                    plan_tasks[t["id"]] = t
        elif ev == "handoff":
            handoffs.append(r)
            if not {"to", "task", "workdir"} <= set(r):
                errs.append(f"{where}: handoff 缺少 to/task/workdir")
        elif ev == "tool":
            if not {"tool", "workdir", "ok"} <= set(r):
                errs.append(f"{where}: tool 事件缺少 tool/workdir/ok")
            path = r.get("path")
            if path:
                if os.path.isabs(path) or ".." in Path(path).parts:
                    errs.append(f"{where}: 写入路径越界 {path}")
                writes[(r.get("workdir"), path)] = True
            db = r.get("db")
            if db:
                if os.path.isabs(db) or ".." in Path(db).parts:
                    errs.append(f"{where}: 数据库路径越界 {db}")
                writes[(r.get("workdir"), db)] = True
        elif ev == "review":
            if r.get("verdict") not in VERDICTS:
                errs.append(f"{where}: 非法 verdict {r.get('verdict')}")
            else:
                reviews.setdefault(r.get("task"), []).append(r["verdict"])
            for d in r.get("defects") or []:
                if not {"file", "why"} <= set(d):
                    errs.append(f"{where}: defect 缺少 file/why")
        elif ev == "fix":
            fixes.add(r.get("task"))
        elif ev == "merge":
            if not {"from_workdir", "files"} <= set(r):
                errs.append(f"{where}: merge 缺少 from_workdir/files")
            merges.append(r)
        elif ev == "deploy":
            if not {"command", "port", "health"} <= set(r):
                errs.append(f"{where}: deploy 缺少 command/port/health")
        elif ev == "test":
            if not {"suite", "passed", "failed", "ok"} <= set(r):
                errs.append(f"{where}: test 缺少 suite/passed/failed/ok")
        elif ev == "run_end":
            if r.get("status") not in STATUSES:
                errs.append(f"{where}: 非法 status {r.get('status')}")
            ended = r

    if len(run_ids) > 1:
        errs.append(f"单个 trace 出现多个 run_id：{sorted(run_ids)}")
    if ended is None:
        errs.append("缺少 run_end 事件")
    if not plan_tasks:
        errs.append("缺少 plan 事件或任务列表为空")

    planned_agents = {t["agent"] for t in plan_tasks.values()}
    for h in handoffs:
        if h.get("task") in plan_tasks and h.get("to") != plan_tasks[h["task"]]["agent"]:
            errs.append(f"handoff 目标 {h.get('to')} 与 plan 中的 {plan_tasks[h['task']]['agent']} 不一致")
    for f in merges:
        for path in f.get("files") or []:
            if (f.get("from_workdir"), path) not in writes:
                errs.append(f"merge 的 {path} 没有对应的写入记录（无法溯源）")
    for task, seq in reviews.items():
        if "changes_requested" in seq:
            if task not in fixes:
                errs.append(f"任务 {task} 被打回但没有任何 fix 记录")
            elif seq[-1] != "approved":
                errs.append(f"任务 {task} 修复后没有复审通过的记录")
    if planned_agents and not any(h.get("to") in planned_agents for h in handoffs):
        errs.append("没有任何 handoff 指向 plan 中的执行者")
    return errs


# ---------------------------------------------------------------- 离线契约检查（13 项）

def offline(entry: Path, board: Board):
    tmp = Path(tempfile.mkdtemp(prefix="lab3-off-"))
    spec = make_spec(make_dataset(1), "http://127.0.0.1:9")
    (tmp / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")

    r = sh([PY, entry, "--help"], tmp)
    text = (r.stdout or "") + (r.stderr or "")
    board.ok(
        r.returncode == 0 and bool(r.stderr.strip()) and all(k in text for k in ("run", "replay", "deploy")),
        f"--help 退出码 0、用法输出到 stderr、列出 run/replay/deploy（实际 {r.returncode}）",
    )

    r = sh([PY, entry, "run"], tmp)
    board.ok(r.returncode == 1, f"缺少 --spec 等用法错误退出码 1（实际 {r.returncode}）")

    r = sh([PY, entry, "nope"], tmp)
    board.ok(r.returncode == 1, f"未知命令退出码 1（实际 {r.returncode}）")

    trace0 = tmp / "zero.jsonl"
    r = sh([PY, entry, "run", "--spec", "spec.json", "--workspace", "ws0",
            "--trace", "zero.jsonl", "--max-steps", "0"], tmp, timeout=180)
    status = None
    if trace0.exists():
        try:
            rows = load_jsonl(trace0)
            status = next((x.get("status") for x in reversed(rows) if x.get("event") == "run_end"), None)
        except Exception:
            status = "PARSE_ERROR"
    board.ok(r.returncode == 2 and status == "step_limit",
             f"--max-steps 0 触发护栏：退出码 2 且 run_end.status=step_limit（实际 {r.returncode}/{status}）")

    # 角色声明
    r = sh([PY, entry, "roles", "--json"], tmp)
    try:
        roles = json.loads(r.stdout)
    except Exception:
        roles = None
    if not isinstance(roles, list):
        board.ok(False, "roles --json 未输出角色数组")
        board.ok(False, "递归派生禁止检查跳过")
        board.ok(False, "reviewer 只读检查跳过")
        board.ok(False, "工具覆盖检查跳过")
    else:
        kinds = {x.get("role") for x in roles}
        orch = next((x for x in roles if x.get("role") == "orchestrator"), None)
        subs = [x for x in roles if x.get("role") != "orchestrator"]
        reviewers = [x for x in roles if x.get("role") == "reviewer"]
        shape = all(
            {"name", "role", "systemPrompt", "allowedTools", "readOnly", "maxSteps"} <= set(x) for x in roles
        )
        board.ok(
            shape and len(roles) >= 6 and {"orchestrator", "collector", "coder", "reviewer"} <= kinds,
            f"角色声明完整（{len(roles)} 个角色，覆盖 orchestrator/collector/coder/reviewer）",
        )
        board.ok(
            bool(orch) and "task" in (orch.get("allowedTools") or [])
            and all("task" not in (x.get("allowedTools") or []) for x in subs),
            "递归派生禁止：只有 orchestrator 拥有 task 工具，子 Agent 均无 task",
        )
        board.ok(
            bool(reviewers) and all(
                x.get("readOnly") is True and not (WRITE_TOOLS & set(x.get("allowedTools") or []))
                for x in reviewers
            ),
            "reviewer 只读且无写工具（第二层防线）",
        )
        union = {t for x in roles for t in (x.get("allowedTools") or [])}
        collectors = [x for x in roles if x.get("role") == "collector"]
        board.ok(REQUIRED_TOOLS <= union and any(
            "http_fetch" in (x.get("allowedTools") or []) for x in collectors),
            f"团队工具覆盖 http_fetch/write_file/run_bash，且 collector 具备 http_fetch"
            f"（缺失：{sorted(REQUIRED_TOOLS - union) or '无'}）")

    # 工具声明（自定义采集工具）
    r = sh([PY, entry, "tools", "--json"], tmp)
    try:
        tools = json.loads(r.stdout)
    except Exception:
        tools = None
    if not isinstance(tools, list) or not tools:
        board.ok(False, "tools --json 未输出工具声明数组")
    else:
        names = {t.get("name") for t in tools}
        shape = all(
            isinstance(t, dict)
            and {"name", "description", "parameters"} <= set(t)
            and isinstance(t.get("parameters"), dict)
            and t["parameters"].get("type") == "object"
            for t in tools
        )
        net = [t for t in tools if t.get("requires_network") is True]
        board.ok(
            shape and REQUIRED_TOOLS <= names and bool(net),
            f"工具声明合法：{len(tools)} 个，含 {sorted(net and [t['name'] for t in net] or [])} 等网络工具",
        )

    # trace validate
    r = sh([PY, entry, "trace", "validate", "--trace", str(FIX / "trace" / "valid.jsonl")], tmp)
    payload = {}
    okv = False
    try:
        payload = json.loads(r.stdout)
        okv = r.returncode == 0 and payload.get("ok") is True and int(payload.get("events", 0)) >= 24
    except Exception:
        pass
    board.ok(okv, f"trace validate 合法样例通过（exit {r.returncode}, events={payload.get('events')}）")

    r = sh([PY, entry, "trace", "validate", "--trace", str(FIX / "trace" / "invalid.jsonl")], tmp)
    errs = []
    try:
        payload = json.loads(r.stdout)
        errs = payload.get("errors") or []
        okv = r.returncode == 1 and payload.get("ok") is False and len(errs) >= 3
    except Exception:
        okv = False
    board.ok(okv, f"trace validate 识别非法样例 ≥3 处（实际 {len(errs)} 处，exit {r.returncode}）")

    # replay
    r = sh([PY, entry, "replay", "--trace", str(FIX / "trace" / "sample.jsonl")], tmp)
    try:
        got = json.loads(r.stdout)
    except Exception:
        got = None
    want = load_json(FIX / "trace" / "sample.expected.json")
    board.ok(got == want, "replay 输出与 sample.expected.json 完全一致（DAG/轮次/产物/部署/自测可重建）")

    # policy
    r = sh([PY, entry, "policy", "test",
            "--policy", str(FIX / "policy" / "policy.json"),
            "--cases", str(FIX / "policy" / "cases.json")], tmp)
    try:
        results = {x["id"]: x.get("decision") for x in json.loads(r.stdout).get("results", [])}
    except Exception:
        results = None
    cases = load_json(FIX / "policy" / "cases.json")
    if results is None:
        board.ok(False, "policy test 未输出可解析的 results")
    else:
        bad = [c["id"] for c in cases if results.get(c["id"]) != c["expect"]]
        board.ok(not bad, f"policy test 判定正确（{len(cases) - len(bad)}/{len(cases)}，错误项：{bad or '无'}）")

    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 真实运行检查（20 项）

def start_crew(entry: Path, root: Path, max_steps: int, seed: int):
    return sh([PY, entry, "run", "--spec", "spec.json", "--workspace", "workspace",
               "--trace", "trace.jsonl", "--max-steps", str(max_steps), "--parallel", "4",
               "--policy", str(FIX / "policy" / "policy.json"), "--seed", str(seed)],
              root, timeout=2400, env={"MAGENT_APPROVE": "auto"})


def stop_service(proc):
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=8)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def wait_health(port, tries=40):
    for _ in range(tries):
        try:
            status, body = http_get(port, "/api/health", timeout=2)
            if status == 200 and json.loads(body).get("ok") is True:
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def live(entry: Path, seed: int, max_steps: int):
    ds = make_dataset(seed)
    p = ds["params"]
    want = expected_summary(ds)
    board = Board(
        f"真实运行 · seed-{seed}（{len(ds['sites'])} 站点 / {len(ds['records'])} 条记录 / "
        f"min_value={p['min_value']} / top_k={p['top_k']} / 精度={p['round_digits']}）"
    )

    tmp = Path(tempfile.mkdtemp(prefix="lab3-live-"))
    src = tmp / "source"
    write_source(ds, src)
    httpd, src_port = serve_dir(src)
    base = f"http://127.0.0.1:{src_port}"

    ws = tmp / "workspace"
    ws.mkdir()
    spec = make_spec(ds, base)
    (tmp / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    sh(["git", "init", "-q"], tmp)
    sh(["git", "add", "-A"], tmp)
    sh(["git", "-c", "user.email=lab@local", "-c", "user.name=lab", "commit", "-qm", "init"], tmp)

    t0 = time.time()
    r = start_crew(entry, tmp, max_steps, seed)
    elapsed = time.time() - t0

    board.section()
    board.ok(r.returncode == 0,
             f"run 退出码 0（实际 {r.returncode}，耗时 {elapsed:.0f}s）"
             + ("" if r.returncode == 0 else f"；stderr 尾部：{(r.stderr or '').strip()[-240:]}"))

    trace = tmp / "trace.jsonl"
    rows = []
    if trace.exists():
        try:
            rows = load_jsonl(trace)
        except Exception as exc:
            board.ok(False, f"trace.jsonl 无法解析：{exc}")
    else:
        board.ok(False, "trace.jsonl 未生成")

    proc = None
    try:
        if not rows:
            for msg in ("trace 结构检查", "角色齐备", "并行派发", "工作目录隔离", "评审闭环", "结果聚合",
                        "run_end", "采集工具调用", "records.jsonl", "summary.json", "app.db",
                        "站点静态资源", "站点 data.json", "服务健康", "/api/summary", "/api/records",
                        "首页渲染", "项目自测", "部署产物", "run_report.json"):
                board.ok(False, f"{msg}：跳过（无 trace）")
            return board

        errs = trace_errors(rows)
        board.ok(not errs, f"trace 结构合法（问题数 {len(errs)}）" + ("" if not errs else f"：{errs[:4]}"))

        roles_present = {x.get("role") for x in rows}
        agents = {x.get("agent"): x.get("role") for x in rows}
        coders = [a for a, role in agents.items() if role == "coder"]
        need = {"orchestrator", "collector", "coder", "reviewer", "integrator", "tester", "deployer"}
        board.ok(need <= roles_present and len(coders) >= 2,
                 f"角色齐备：collector/coder×{len(coders)}/reviewer/integrator/tester/deployer")

        first_tool = next((i for i, x in enumerate(rows) if x.get("event") == "tool"), len(rows))
        early = [x for x in rows[:first_tool] if x.get("event") == "handoff"]
        board.ok(len(early) >= 2, f"并行派发：首个工具调用前已有 {len(early)} 个 handoff")

        wds = {x.get("workdir") for x in rows if x.get("event") == "tool" and x.get("workdir")}
        board.ok(len(wds) >= 3 and all(str(w).startswith("agents/") for w in wds),
                 f"工作目录隔离：{len(wds)} 个独立 workdir（{sorted(wds)[:3]}…）")

        task_reviews = {}
        for x in rows:
            if x.get("event") == "review":
                task_reviews.setdefault(x.get("task"), []).append(x.get("verdict"))
        loop_ok = any(
            seq and seq[0] == "changes_requested" and seq[-1] == "approved"
            for seq in task_reviews.values()
        )
        board.ok(loop_ok, "评审回路：出现 changes_requested → 修复 → approved")

        merges = [x for x in rows if x.get("event") == "merge"]
        files = sorted({f for m in merges for f in (m.get("files") or [])})
        board.ok(len(merges) >= 3 and len(files) >= 8,
                 f"结果聚合：{len(merges)} 次 merge、{len(files)} 个文件")
        missing = [f for f in files if not (ws / f).is_file()]
        board.ok(not missing, f"merge 清单中的文件都落在 workspace（缺失：{missing[:5] or '无'}）")

        end = next((x for x in reversed(rows) if x.get("event") == "run_end"), {})
        board.ok(end.get("status") == "ok" and int(end.get("steps") or 0) <= max_steps,
                 f"run_end：status={end.get('status')}，steps={end.get('steps')}（上限 {max_steps}）")

        fetches = [x for x in rows if x.get("event") == "tool" and x.get("tool") == "http_fetch"]
        okf = [x for x in fetches if x.get("ok") is True]
        board.ok(len(okf) >= len(ds["sites"]),
                 f"采集工具真实调用：http_fetch 成功 {len(okf)} 次（站点 {len(ds['sites'])} 个）")

        # ---- 数据产物
        rec_path = ws / "data" / "records.jsonl"
        got_recs = []
        if rec_path.is_file():
            try:
                got_recs = load_jsonl(rec_path)
            except Exception:
                got_recs = []
        norm = lambda xs: sorted(
            ({"site": x.get("site"), "device": x.get("device"), "day": x.get("day"),
              "value": round(float(x.get("value", -1)), 1)} for x in xs),
            key=lambda x: (x["site"], x["device"], x["day"]),
        )
        want_recs = norm(ds["records"])
        rec_ok = len(got_recs) == len(want_recs) and norm(got_recs) == want_recs
        board.ok(rec_ok, f"records.jsonl：{len(got_recs)} 条（期望 {len(want_recs)}），逐条与源数据一致")

        sum_path = ws / "data" / "summary.json"
        got_sum = None
        if sum_path.is_file():
            try:
                got_sum = load_json(sum_path)
            except Exception:
                got_sum = None
        if got_sum is None:
            board.ok(False, "summary.json 缺失或无法解析")
        else:
            diffs = diff_obj(got_sum, want)
            board.ok(not diffs, "summary.json 与独立标准答案一致"
                     + ("" if not diffs else f"（差异 {len(diffs)} 处：{diffs[:4]}）"))

        db_path = ws / "data" / "app.db"
        db_ok, db_msg = False, "app.db 不存在"
        if db_path.is_file():
            try:
                conn = sqlite3.connect(str(db_path))
                tables = {row[0] for row in conn.execute(
                    "select name from sqlite_master where type='table'")}
                n_rows = conn.execute("select count(*) from records").fetchone()[0]
                total = conn.execute("select sum(value) from records").fetchone()[0] or 0.0
                n_sum = conn.execute("select count(*) from summary").fetchone()[0]
                conn.close()
                db_ok = ({"records", "summary"} <= tables
                         and n_rows == len(ds["records"])
                         and abs(round(total, p["round_digits"]) - round(sum(
                             x["value"] for x in ds["records"]), p["round_digits"])) < 1e-6
                         and n_sum >= 3)
                db_msg = f"表 {sorted(tables)}，records {n_rows} 行，summary {n_sum} 行，合计 {round(total, 2)}"
            except Exception as exc:
                db_msg = f"读取失败：{type(exc).__name__}: {exc}"
        board.ok(db_ok, f"app.db：{db_msg}")

        # ---- 前端站点
        site = ws / "site"
        i_html = site / "index.html"
        i_text = i_html.read_text(encoding="utf-8", errors="replace") if i_html.is_file() else ""
        js = site / "app.js"
        css = site / "style.css"
        static_ok = (
            i_html.is_file() and "<html" in i_text.lower()
            and "app.js" in i_text and "style.css" in i_text
            and js.is_file() and js.stat().st_size >= 400
            and css.is_file() and css.stat().st_size >= 200
        )
        board.ok(static_ok, "站点四件套：index.html 引用 app.js/style.css，且脚本与样式非空壳")

        data_json = site / "data.json"
        dj_ok = False
        if data_json.is_file():
            try:
                payload = json.loads(data_json.read_text(encoding="utf-8"))
                sub = payload.get("summary") if isinstance(payload, dict) else None
                recs = payload.get("records") if isinstance(payload, dict) else None
                dj_ok = (isinstance(sub, dict) and isinstance(recs, list)
                         and abs(float(sub.get("total", -1)) - want["total"]) < 1e-6
                         and len(recs) == len(ds["records"]))
            except Exception:
                dj_ok = False
        board.ok(dj_ok, "site/data.json：summary.total 与记录数与标准答案一致（前端数据不是写死的）")

        # ---- 后端服务
        port = free_port()
        proc = subprocess.Popen(
            [PY, "server/app.py", "--port", str(port)], cwd=str(ws),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        healthy = wait_health(port)
        board.ok(healthy, f"后端启动并 /api/health 返回 ok（端口 {port}）")

        if healthy:
            try:
                status, body = http_get(port, "/api/summary")
                payload = json.loads(body)
                sub = payload.get("summary", payload)
                diffs = diff_obj(sub, want)
                board.ok(status == 200 and not diffs,
                         "/api/summary 与标准答案一致"
                         + ("" if not diffs else f"（差异 {len(diffs)} 处：{diffs[:3]}）"))
            except Exception as exc:
                board.ok(False, f"/api/summary 请求失败：{exc}")

            site_name = ds["sites"][0]
            try:
                status, body = http_get(port, f"/api/records?site={site_name}&limit=2")
                payload = json.loads(body)
                recs = payload.get("records") or []
                expect = [r for r in ds["records"] if r["site"] == site_name]
                expect.sort(key=lambda x: (x["device"], x["day"]))
                okr = (status == 200 and len(recs) == min(2, len(expect))
                       and all(r.get("site") == site_name for r in recs))
                board.ok(okr, f"/api/records?site={site_name}&limit=2 过滤与截断正确（返回 {len(recs)} 条）")
            except Exception as exc:
                board.ok(False, f"/api/records 请求失败：{exc}")

            try:
                status, body = http_get(port, "/")
                board.ok(status == 200 and "<html" in body.lower() and "app.js" in body,
                         "GET / 返回站点首页（后端能托管前端）")
            except Exception as exc:
                board.ok(False, f"GET / 请求失败：{exc}")
        else:
            for msg in ("/api/summary", "/api/records", "首页渲染"):
                board.ok(False, f"{msg}：跳过（服务未就绪）")

        # ---- 自测与部署
        ut = sh([PY, "-m", "unittest", "discover", "-s", "tests", "-q"], ws, timeout=300)
        m = re.search(r"Ran (\d+) tests?", (ut.stderr or "") + (ut.stdout or ""))
        n_tests = int(m.group(1)) if m else 0
        board.ok(ut.returncode == 0 and n_tests >= 5,
                 f"项目自测：unittest 全绿且 ≥5 个用例（实际 {n_tests} 个，exit {ut.returncode}）")

        deploy_readme = ws / "deploy" / "README.md"
        d_text = deploy_readme.read_text(encoding="utf-8", errors="replace") if deploy_readme.is_file() else ""
        deploy_evt = [x for x in rows if x.get("event") == "deploy"]
        test_evt = [x for x in rows if x.get("event") == "test"]
        board.ok(
            bool(d_text) and "server/app.py" in d_text and "/api/health" in d_text
            and any(x.get("health") is True for x in deploy_evt)
            and any(x.get("ok") is True for x in test_evt),
            f"部署与自测留痕：deploy 事件 {len(deploy_evt)} 条、test 事件 {len(test_evt)} 条、deploy/README.md 含启动命令",
        )

        report = tmp / "run_report.json"
        okr = False
        if report.is_file():
            try:
                rep = load_json(report)
                okr = {"run_id", "status", "agents", "tasks"} <= set(rep) and isinstance(rep.get("agents"), list)
            except Exception:
                okr = False
        board.ok(okr, "run_report.json 含 run_id/status/agents/tasks")
    finally:
        stop_service(proc)
        httpd.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    return board


# ---------------------------------------------------------------- 入口

def main():
    ap = argparse.ArgumentParser(description="实验三 · 多 Agent 数据应用工厂验收")
    ap.add_argument("entry", help="学生实现入口，如 magent.py")
    ap.add_argument("--offline", action="store_true", help="只跑离线契约检查（默认）")
    ap.add_argument("--live", action="store_true", help="跑真实任务（需要模型 / Key）")
    ap.add_argument("--both", action="store_true", help="离线 + 真实任务")
    ap.add_argument("--seed", type=int, default=None, help="规格 seed（默认随机）")
    ap.add_argument("--max-steps", type=int, default=80, help="交给被测工具的步数上限（默认 80）")
    ap.add_argument("--seeds", type=int, default=1, help="真实任务跑几套随机规格（教师抽查用）")
    args = ap.parse_args()

    entry = Path(args.entry).resolve()
    if not entry.is_file():
        print(f"❌ 找不到入口文件：{entry}")
        return 1

    do_off = args.offline or args.both or not (args.live or args.both)
    do_live = args.live or args.both

    boards = []
    if do_off:
        b = Board("离线契约检查（不调用模型）")
        b.section()
        offline(entry, b)
        boards.append(b)

    if do_live:
        if args.seed is None:
            rnd = random.Random(time.time_ns())
            seeds = [rnd.randint(1000, 9999) for _ in range(max(1, args.seeds))]
        else:
            seeds = [args.seed + i for i in range(max(1, args.seeds))]
        for s in seeds:
            boards.append(live(entry, s, args.max_steps))

    total = sum(b.total for b in boards)
    passed = sum(b.passed for b in boards)
    print(f"\n{'🎉 全部通过' if passed == total else '尚未通过'} —— {passed}/{total} 项")
    if passed != total:
        print("对照 ❌ 项修改后重跑；契约细节见《实验三-作业要求.md》第三节。")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
