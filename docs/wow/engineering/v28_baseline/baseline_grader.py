#!/usr/bin/env python3
"""V28 before-era baseline grader — commit-level census over a git window.

Classifies each non-merge commit R/P/C from the files it changes (V28 §10),
then measures the behavioural proxies V28 mandates: mechanism stated, test
touched with the fix (red-first proxy), same-cause re-fix within 72h, revert,
diff size, unsupported completion claim.

Commit-level proxies are NOT PR timelines. Metrics needing review comments,
check runs or final-message endings are reported UNAVAILABLE, never estimated.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
from typing import Any

FLD = "\x1f"
REC = "\x1e"

TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__|spec)/"
    r"|(^|/)test_[^/]*\.(py|ts|tsx|js|jsx)$"
    r"|_test\.(py|ts|tsx|js|jsx)$"
    r"|\.(test|spec)\.(ts|tsx|js|jsx)$",
    re.I,
)
MECHANISM = re.compile(
    r"\b(because|caused by|root cause|due to|so that|mechanism|"
    r"regression from|originates? from)\b",
    re.I,
)
FIX_TITLE = re.compile(
    r"(^|[^a-z])(fix|fixes|fixed|hotfix|repair|repaired|correct|corrects|"
    r"corrected|resolve|resolves|resolved)([^a-z]|$)",
    re.I,
)
REVERT_TITLE = re.compile(r"^revert\b", re.I)
CLAIM = re.compile(r"\b(fixed|deployed|resolved|complete[ds]?|shipped|done)\b", re.I)
SHA = re.compile(r"\b[0-9a-f]{7,40}\b", re.I)

PROTECTED_LEAF = re.compile(
    r"^(codeowners|.*ruleset.*|.*trust[_-]?root.*|.*trust.*|.*credential.*|"
    r".*secret.*|.*permission.*|.*release[_-]safety.*)$",
    re.I,
)
# --- What counts as probability-producing code (C) ---------------------------
# C is reserved for code whose own contract is to define, fit, compute or emit a
# published probability. Three kinds of evidence, checked in this order, and
# every explicit path lives in the one table below:
#
#   1. TREES   whole trees that are probability by contract.
#   2. MODULES one (scope, leaves) table for modules that produce or publish a
#              probability but whose names dodge the heuristic in (3). This is
#              the only place to edit when an audit finds a new one. Scopes keep
#              a leaf in one package from sweeping a same-named file elsewhere.
#   3. NAMES   the broad heuristic for the long tail: substrings that reliably
#              appear in probability code, plus `train_*` trainers.
#
# TREES and NAMES share `caught_by_heuristic`, so the fixture that keeps MODULES
# a list of exceptions asks the same predicate the classifier uses.
#
# `v17` must match a real path segment: `artifacts/wow-engine/v17/x.py` AND the
# top-level `v17/` tree. A bare substring misses `v17/...` at repo root.
PROBABILITY_TREES = (re.compile(r"(^|/)v17(/|$)", re.I),)
PROBABILITY_MODULES = (
    # Governate runtime root: engines, publishers, board and pick-request scorers.
    (
        "artifacts/wow-engine/",
        frozenset(
            {
                "engine.py",
                "simulation.py",
                "prop_discrete_engine.py",
                "api_prod.py",
                "api_prod_market.py",
                "api_g11.py",
                "pick_request_pipeline.py",
                "pick_request_runtime.py",
                "pick_request_runtime_core.py",
            }
        ),
    ),
    # Legacy scoring surface: this one Flask app builds and serves model_probability.
    ("artifacts/flask-scoring-api/", frozenset({"app.py"})),
    # Gate engine: board scoring plus the composite/confidence pipeline.
    (
        "artifacts/flask-scoring-api/gate_engine/",
        frozenset(
            {
                "pipeline.py",
                "full_board_confidence.py",
                "component_composite.py",
                "daily_orchestrator.py",
                "wnba_generative_gate.py",
            }
        ),
    ),
    # Kalshi weather: p = normal_cdf(threshold; central_estimate + bias, sigma),
    # so the modules supplying the distribution's location/spread, emitting the
    # package, and the fail-closed null package all move the published number.
    # Only the three that nothing else catches are listed: models.py,
    # probability_core.py and calibration_fit.py already match NAMES below.
    (
        "artifacts/wow-engine/kalshi_weather_v2/",
        frozenset(
            {
                "hourly_forecast_fusion.py",
                "daily_high_forecast_fusion.py",
                "runtime.py",
            }
        ),
    ),
)
PROBABILITY_NAME_TOKENS = (
    "calibrat",
    "/model",
    "model_",
    "_model",
    "probab",
    "specialist",
    "/scoring/",
    "fitted",
    "pmf",
    "logistic",
    "trainer",
    "ranker",
)
PROBABILITY_NAME_PREFIXES = ("train_",)
# Deliberately NOT C, despite the shape of their names: `*_engine.py` that is not
# an emitter (contradiction_engine.py, freshness_engine.py, voi_engine.py),
# market.py, prop_settlement.py, ledger.py, the `v17_*.py` schema/probe/telemetry
# siblings, and kalshi_weather_v2's orchestrator.py/agents.py -- these consume a
# finished ProbabilityPackage, or are evidence/IO/decision/settlement code.


def caught_by_heuristic(low: str) -> str | None:
    """The TREES/NAMES tier that marks `low` as C without the MODULES table.

    Returns a label naming the rule that matched, or None. A MODULES row whose
    path matches here is redundant: it claims coverage the broad heuristic was
    already giving by accident of naming.
    """
    if any(tree.search(low) for tree in PROBABILITY_TREES):
        return "PROBABILITY_TREES"
    for token in PROBABILITY_NAME_TOKENS:
        if token in low:
            return f"PROBABILITY_NAME_TOKENS[{token!r}]"
    if low.rsplit("/", 1)[-1].startswith(PROBABILITY_NAME_PREFIXES):
        return "PROBABILITY_NAME_PREFIXES"
    return None


def classify_path(path: str) -> str:
    """P protected, C probability-producing, R routine. P wins over C over R."""
    p = (path or "").strip()
    if not p:
        return "R"
    low = p.lower().replace("\\", "/")
    if low.startswith(".github/") or "/.github/" in low:
        return "P"
    leaf = low.rsplit("/", 1)[-1]
    if PROTECTED_LEAF.match(leaf) or "/permissions/" in low:
        return "P"
    if low.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".sql")):
        parts = low.split("/")
        if "docs" not in parts and not TEST_PATH.search(low):
            if any(
                low.startswith(scope) and leaf in leaves
                for scope, leaves in PROBABILITY_MODULES
            ) or caught_by_heuristic(low):
                return "C"
    return "R"


def classify_commit(paths: list[str]) -> str:
    classes = {classify_path(p) for p in paths}
    if not classes:
        return "R"
    for c in ("P", "C", "R"):
        if c in classes:
            return c
    return "R"


def has_mechanism(subject: str, body: str) -> bool:
    return bool(MECHANISM.search(subject or "") or MECHANISM.search(body or ""))


def is_fix_title(subject: str) -> bool:
    return bool(FIX_TITLE.search(subject or ""))


def is_revert(subject: str) -> bool:
    return bool(REVERT_TITLE.search(subject or ""))


def unsupported_claim(subject: str, body: str, test_touched: bool) -> bool:
    text = f"{subject}\n{body}"
    return bool(CLAIM.search(text)) and not SHA.search(text) and not test_touched


def parse_metadata(text: str) -> list[dict[str, Any]]:
    recs = []
    for chunk in text.split(REC):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        parts = chunk.split(FLD)
        if len(parts) < 4:
            continue
        recs.append(
            {
                "hash": parts[0].strip(),
                "ts": int(parts[1]),
                "subject": parts[2].strip(),
                "body": FLD.join(parts[3:]).strip(),
            }
        )
    return recs


def parse_numstat(text: str) -> dict[str, list[tuple[str, int | None, int | None]]]:
    files: dict[str, list[tuple[str, int | None, int | None]]] = {}
    for chunk in text.split(REC):
        lines = [ln for ln in chunk.splitlines() if ln.strip()]
        if not lines:
            continue
        h = lines[0].strip()
        entries: list[tuple[str, int | None, int | None]] = []
        for ln in lines[1:]:
            cols = ln.split("\t")
            if len(cols) != 3:
                continue
            a, d, path = cols
            entries.append(
                (path, None if a == "-" else int(a), None if d == "-" else int(d))
            )
        files[h] = entries
    return files


def build_records(meta: list[dict[str, Any]], numstat: dict[str, list]) -> list[dict[str, Any]]:
    out = []
    for m in meta:
        entries = numstat.get(m["hash"], [])
        paths = [p for p, _, _ in entries]
        test_touched = any(TEST_PATH.search(p) for p in paths)
        src_touched = any(not TEST_PATH.search(p) for p in paths)
        size = sum((a or 0) + (d or 0) for _, a, d in entries)
        out.append(
            {
                **m,
                "class": classify_commit(paths),
                "paths": paths,
                "test_touched": test_touched,
                "src_touched": src_touched,
                "red_first": test_touched and src_touched,
                "size": size,
                "mechanism": has_mechanism(m["subject"], m["body"]),
                "fix_title": is_fix_title(m["subject"]),
                "revert": is_revert(m["subject"]),
                "unsupported_claim": unsupported_claim(
                    m["subject"], m["body"], test_touched
                ),
            }
        )
    return out


def rework_events(records: list[dict[str, Any]], window_s: int = 72 * 3600) -> dict[str, Any]:
    """Same-file re-fix inside 72h, counting only fix-titled commits."""
    last: dict[str, int] = {}
    events = 0
    involved: set[str] = set()
    for r in sorted(records, key=lambda x: x["ts"]):
        if not r["fix_title"]:
            continue
        for p in r["paths"]:
            prev = last.get(p)
            if prev is not None and 0 < r["ts"] - prev <= window_s:
                events += 1
                involved.add(r["hash"])
            last[p] = r["ts"]
    return {"events": events, "commits": len(involved)}


def rate(n: int, d: int) -> float | None:
    return round(n / d, 4) if d else None


def compute_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {"total": len(records), "classes": {}}
    for cls in ("R", "P", "C"):
        rows = [r for r in records if r["class"] == cls]
        n = len(rows)
        rw = rework_events(rows)
        fixes = sum(1 for r in rows if r["fix_title"])
        sizes = [r["size"] for r in rows if r["size"] > 0]
        result["classes"][cls] = {
            "commits": n,
            "mechanism": sum(1 for r in rows if r["mechanism"]),
            "mechanism_rate": rate(sum(1 for r in rows if r["mechanism"]), n),
            "red_first": sum(1 for r in rows if r["red_first"]),
            "red_first_rate": rate(sum(1 for r in rows if r["red_first"]), n),
            "fix_titled": fixes,
            "fix_rate": rate(fixes, n),
            "reverts": sum(1 for r in rows if r["revert"]),
            "rework_events": rw["events"],
            "rework_commits": rw["commits"],
            "rework_per_fix": rate(rw["events"], fixes),
            "unsupported_claim": sum(1 for r in rows if r["unsupported_claim"]),
            "unsupported_claim_rate": rate(
                sum(1 for r in rows if r["unsupported_claim"]), n
            ),
            "median_lines_changed": statistics.median(sizes) if sizes else None,
        }
    result["unavailable"] = [
        "mechanism-before-first-edit (needs PR body edit timing, not git)",
        "red-before-green proof (needs a recorded failing run, not a diff)",
        "stop-despite-available-rung (needs final messages / handoff docs)",
        "force-push count (needs reflog/PR events)",
        "check-level first-pass rate (needs check runs)",
    ]
    return result


def git(*args: str, cwd: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def load(cwd: str, since: str, until: str) -> tuple[list[dict], int]:
    meta = parse_metadata(
        git(
            "log",
            f"--since={since}",
            f"--until={until}",
            "--no-merges",
            f"--format=%H{FLD}%ct{FLD}%s{FLD}%b{REC}",
            cwd=cwd,
        )
    )
    numstat = parse_numstat(
        git(
            "log",
            f"--since={since}",
            f"--until={until}",
            "--no-merges",
            f"--format={REC}%H",
            "--numstat",
            cwd=cwd,
        )
    )
    merges = int(
        git(
            "rev-list",
            "--count",
            "--merges",
            f"--since={since}",
            f"--until={until}",
            "HEAD",
            cwd=cwd,
        ).strip()
        or 0
    )
    return build_records(meta, numstat), merges


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd", default=".")
    ap.add_argument("--since", default="2026-08-06T00:00:00Z")
    ap.add_argument("--until", default="2026-11-04T14:07:05Z")
    ap.add_argument("--json", default="")
    args = ap.parse_args(argv)

    records, merges = load(args.cwd, args.since, args.until)
    metrics = compute_metrics(records)
    metrics["window"] = {"since": args.since, "until": args.until}
    metrics["merges_excluded"] = merges

    print(f"window {args.since} .. {args.until}")
    print(f"non-merge commits graded: {metrics['total']}   merges excluded: {merges}")
    print()
    hdr = f"{'class':6}{'commits':>8}{'mech%':>8}{'redfirst%':>11}{'fix%':>7}{'revert':>7}{'rework/fix':>12}{'claim%':>8}{'med_lines':>10}"
    print(hdr)
    print("-" * len(hdr))
    for cls in ("R", "P", "C"):
        c = metrics["classes"][cls]
        def fmt(v, pct=False):
            if v is None:
                return "n/a"
            return f"{v*100:.1f}" if pct else f"{v}"
        print(
            f"{cls:6}{c['commits']:>8}{fmt(c['mechanism_rate'], True):>8}"
            f"{fmt(c['red_first_rate'], True):>11}{fmt(c['fix_rate'], True):>7}"
            f"{c['reverts']:>7}{fmt(c['rework_per_fix']):>12}"
            f"{fmt(c['unsupported_claim_rate'], True):>8}"
            f"{('n/a' if c['median_lines_changed'] is None else int(c['median_lines_changed'])):>10}"
        )
    print()
    print("UNAVAILABLE (needs PR timelines, not git):")
    for u in metrics["unavailable"]:
        print(f"  - {u}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"metrics": metrics, "classes": metrics["classes"]}, fh, indent=2)
        print(f"\njson: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
