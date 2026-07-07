#!/usr/bin/env python3
"""
smoke_test.py — WOW Data Hub Daily Smoke Test CLI

Usage:
    python scripts/smoke_test.py
    python scripts/smoke_test.py --api http://localhost:8080/api --flask http://localhost:25643

Never prints API keys. Returns exit code 0 on PASS/WARN, 1 on FAIL.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import Any

try:
    import urllib.request
    import urllib.error
except ImportError:
    sys.exit("Standard library not available")

# ── Config ────────────────────────────────────────────────────────────────────
DEFAULT_API   = os.environ.get("API_SERVER_URL", "http://localhost:8080/api")
DEFAULT_FLASK = os.environ.get("SCORING_API_URL", "http://localhost:25643")
SCORING_KEY   = os.environ.get("SCORING_API_KEY", "")

# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _req(method: str, url: str, body: dict | None = None,
         headers: dict | None = None, timeout: int = 10) -> dict:
    data = json.dumps(body).encode() if body else None
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode(errors="replace")[:200]
        raise RuntimeError(f"HTTP {e.code}: {body_txt}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Connection failed: {e.reason}")


def get(url: str, **kw) -> dict:
    return _req("GET", url, **kw)


def post(url: str, body: dict, **kw) -> dict:
    return _req("POST", url, body=body, **kw)


# ── Check runner ──────────────────────────────────────────────────────────────

class Check:
    def __init__(self, name: str, status: str, details: str):
        self.name    = name
        self.status  = status  # PASS | WARN | FAIL
        self.details = details

    def __repr__(self):
        icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}.get(self.status, "•")
        return f"  {icon} [{self.status}] {self.name}: {self.details}"


def check(name: str, fn, critical: bool = True) -> Check:
    try:
        details = fn()
        return Check(name, "PASS", details)
    except Exception as e:
        status = "FAIL" if critical else "WARN"
        return Check(name, status, str(e)[:200])


# ── Individual checks ─────────────────────────────────────────────────────────

def _flask_health(flask: str) -> str:
    d = get(f"{flask}/health")
    if d.get("status") != "ok":
        raise RuntimeError(f"Flask health not ok: {d}")
    return "Flask /health OK"


def _providers(api: str) -> str:
    d = get(f"{api}/props/providers")
    configured = [k for k, v in d.get("providers", {}).items() if v.get("configured")]
    return f"{len(configured)}/{len(d.get('providers',{}))} providers configured: {configured}"


def _normalize_mock(api: str) -> tuple[str, list]:
    d = post(f"{api}/props/normalize", {"sport": "NBA", "providers": ["mock"]})
    props = d.get("props", [])
    if not d.get("data_unobtainable"):
        raise RuntimeError("data_unobtainable flag missing from mock response")
    bad = [p for p in props if p.get("source_status") != "DATA_UNOBTAINABLE"]
    if bad:
        raise RuntimeError(f"{len(bad)} mock props NOT labeled DATA_UNOBTAINABLE")
    return f"{len(props)} mock props all labeled DATA_UNOBTAINABLE ✓", props


def _mock_cannot_approve(api: str, mock_props: list) -> str:
    if not mock_props:
        raise RuntimeError("No mock props to test")
    d = post(f"{api}/props/score-batch", {"props": mock_props[:2]})
    results = d.get("results", [])
    MONEY = {"MONEY_QUALIFIED", "FINAL_APPROVED"}
    approvals = [r for r in results if r.get("result", {}).get("terminal_label") in MONEY]
    if approvals:
        raise RuntimeError(f"SAFETY FAIL: {len(approvals)} mock props reached approval")
    if d.get("execution_rule") != "READ_ONLY_NO_EXECUTION":
        raise RuntimeError(f"execution_rule is not READ_ONLY_NO_EXECUTION: {d.get('execution_rule')}")
    return f"{len(results)} mock props all blocked, READ_ONLY_NO_EXECUTION ✓"


def _row_count(api: str, mock_props: list) -> str:
    sample = mock_props[:3]
    if not sample:
        return "Skipped — no mock props"
    d = post(f"{api}/props/score-batch", {"props": sample})
    out = d.get("count") or len(d.get("results", []))
    if len(sample) != out:
        raise RuntimeError(f"ROW COUNT MISMATCH: sent {len(sample)}, got {out}")
    return f"{len(sample)} in → {out} out ✓"


def _odds_api_live(api: str) -> str:
    prov = get(f"{api}/props/providers")
    if not prov.get("providers", {}).get("odds_api", {}).get("configured"):
        raise RuntimeError("ODDS_API_KEY not configured")
    d = post(f"{api}/props/normalize",
             {"sport": "baseball_mlb", "providers": ["odds_api"]}, timeout=25)
    if d.get("data_unobtainable"):
        raise RuntimeError("Odds API returned no live data")
    return f"{len(d.get('props', []))} live MLB props from Odds API ✓"


def _request_log(flask: str) -> str:
    d = get(f"{flask}/request-log?limit=1",
            headers={"X-API-Key": SCORING_KEY} if SCORING_KEY else {})
    return f"request-log OK — {d.get('count','?')} total entries"


def _leaderboard(flask: str) -> str:
    d = get(f"{flask}/leaderboard")
    return "leaderboard OK"


def _mcp_package() -> str:
    import importlib.util, pathlib
    mcp_pkg = pathlib.Path(__file__).parent.parent.parent / "mcp-server" / "package.json"
    if not mcp_pkg.exists():
        raise RuntimeError(f"mcp-server/package.json not found at {mcp_pkg}")
    pkg = json.loads(mcp_pkg.read_text())
    if "@modelcontextprotocol/sdk" not in pkg.get("dependencies", {}):
        raise RuntimeError("@modelcontextprotocol/sdk missing from mcp-server deps")
    return f"MCP package OK: {pkg.get('name')} ✓"


def _sim_data_unobtainable(api: str) -> str:
    d = post(f"{api}/dev/source-status-sim", {
        "source_status": "DATA_UNOBTAINABLE",
        "sport": "WNBA", "prop_type": "points", "side": "LESS", "line": 20.5,
    }, timeout=20)
    if d.get("can_reach_money_qualified") is not False:
        raise RuntimeError("Simulator says DATA_UNOBTAINABLE CAN reach money")
    if not d.get("safety_check", {}).get("safety_gate_held"):
        raise RuntimeError("Safety gate did NOT hold for DATA_UNOBTAINABLE")
    return f"DATA_UNOBTAINABLE → {d.get('terminal_bucket')}, safety_gate_held=true ✓"


def _sim_source_conflict(api: str) -> str:
    d = post(f"{api}/dev/source-status-sim", {
        "source_status": "SOURCE_CONFLICT",
        "sport": "MLB", "prop_type": "pitcher strikeouts",
        "side": "MORE", "line": 6.5,
    }, timeout=20)
    if d.get("can_reach_final_approved") is not False:
        raise RuntimeError("Simulator says SOURCE_CONFLICT CAN reach final approval")
    if not d.get("safety_check", {}).get("safety_gate_held"):
        raise RuntimeError("Safety gate did NOT hold for SOURCE_CONFLICT")
    return f"SOURCE_CONFLICT → {d.get('terminal_bucket')}, safety_gate_held=true ✓"


# ── Main ─────────────────────────────────────────────────────────────────────

def run(api: str, flask: str) -> dict[str, Any]:
    ts   = datetime.utcnow().isoformat() + "Z"
    t0   = time.time()
    checks: list[Check] = []

    print(f"\n🔍 WOW Data Hub Smoke Test — {ts}")
    print(f"   API: {api}  Flask: {flask}\n")

    # Run checks
    checks.append(check("flask_health",        lambda: _flask_health(flask)))
    checks.append(check("props_providers",     lambda: _providers(api)))

    # Normalize mock (need result for downstream checks)
    mock_result = ("", [])
    try:
        mock_result = _normalize_mock(api)
        checks.append(Check("props_normalize_mock", "PASS", mock_result[0]))
    except Exception as e:
        checks.append(Check("props_normalize_mock", "FAIL", str(e)))

    mock_props = mock_result[1]
    checks.append(check("mock_cannot_approve",     lambda: _mock_cannot_approve(api, mock_props)))
    checks.append(check("row_count_reconciliation",lambda: _row_count(api, mock_props)))
    checks.append(check("odds_api_live",           lambda: _odds_api_live(api), critical=False))
    checks.append(check("request_log",             lambda: _request_log(flask)))
    checks.append(check("leaderboard",             lambda: _leaderboard(flask)))
    checks.append(check("mcp_server_package",      _mcp_package))
    checks.append(check("sim_data_unobtainable",   lambda: _sim_data_unobtainable(api)))
    checks.append(check("sim_source_conflict",     lambda: _sim_source_conflict(api)))

    # Print results
    for c in checks:
        print(repr(c))

    elapsed = round(time.time() - t0, 2)
    passes   = [c for c in checks if c.status == "PASS"]
    warns    = [c for c in checks if c.status == "WARN"]
    failures = [c for c in checks if c.status == "FAIL"]

    overall = "FAIL" if failures else "WARN" if warns else "PASS"
    icon    = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}[overall]

    print(f"\n{icon} Overall: {overall}  "
          f"({len(passes)} pass / {len(warns)} warn / {len(failures)} fail)  "
          f"[{elapsed}s]")

    if failures:
        print("\n❌ Critical failures:")
        for f in failures:
            print(f"   • {f.name}: {f.details}")

    return {
        "status":    overall,
        "timestamp": ts,
        "elapsed_s": elapsed,
        "checks_pass": len(passes),
        "checks_warn": len(warns),
        "checks_fail": len(failures),
        "checks": [{"name": c.name, "status": c.status, "details": c.details} for c in checks],
        "critical_failures": [c.name for c in failures],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WOW Data Hub Smoke Test")
    parser.add_argument("--api",   default=DEFAULT_API,   help="API server base URL")
    parser.add_argument("--flask", default=DEFAULT_FLASK, help="Flask scoring API base URL")
    parser.add_argument("--json",  action="store_true",   help="Emit JSON report to stdout")
    args = parser.parse_args()

    result = run(args.api, args.flask)

    if args.json:
        print(json.dumps(result, indent=2))

    sys.exit(0 if result["status"] in ("PASS", "WARN") else 1)
