#!/usr/bin/env python3
"""Fast, deterministic engineering helpers for WOW V17.

This module accelerates engineering reproduction, test selection, sharding, and
read-only production verification. It never produces sporting probability and
never grants execution authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable, Sequence

ENGINE_ROOT = Path(__file__).resolve().parents[1]
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
RUNTIME_GENERATION = "V17_ACTIVE"

PYTEST_PATTERNS = ("test_*.py", "*_test.py", "*_tests.py")
IGNORED_PARTS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache"}

GOVERNANCE_TESTS = (
    "tests/test_v17_governance_parity_contract.py",
    "tests/test_v17_full_board_typed_failure_preservation.py",
    "tests/test_v17_card_immutable_receipt_gate.py",
    "tests/test_v17_prop_canonical_identity.py",
    "tests/test_v17_team_event_probability_preservation.py",
    "tests/test_v17_top10_model_reconciliation.py",
)

LANE_TOKENS = {
    "mlb": ("mlb", "baseball"),
    "nfl": ("nfl", "football"),
    "wnba": ("wnba",),
    "ncaaf": ("ncaaf",),
    "ncaab": ("ncaab",),
    "nba": ("nba",),
    "nhl": ("nhl", "hockey"),
    "soccer": ("soccer",),
    "tennis": ("tennis",),
    "mma": ("mma",),
    "pga": ("pga", "golf"),
}


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def discover_test_files(root: Path) -> list[str]:
    """Return pytest-discoverable files using WOW's configured filename patterns."""
    root = root.resolve()
    found: set[str] = set()
    for pattern in PYTEST_PATTERNS:
        for path in root.rglob(pattern):
            if not path.is_file() or any(part in IGNORED_PARTS for part in path.parts):
                continue
            found.add(_relative(path, root))
    return sorted(found)


def shard_test_files(files: Sequence[str], index: int, total: int) -> list[str]:
    """Deterministically distribute whole test files across isolated runners."""
    if total < 1:
        raise ValueError("total must be >= 1")
    if index < 0 or index >= total:
        raise ValueError("index must satisfy 0 <= index < total")
    selected: list[str] = []
    for path in sorted(set(files)):
        bucket = int(hashlib.sha256(path.encode("utf-8")).hexdigest()[:16], 16) % total
        if bucket == index:
            selected.append(path)
    return selected


def _changed_files(base: str, head: str, cwd: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return sorted({line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()})


def select_affected_tests(changed_paths: Iterable[str], engine_root: Path = ENGINE_ROOT) -> list[str]:
    """Select a conservative fast regression set from changed paths.

    Governance tests always run for engine changes. Sport-specific changes add
    every test filename containing the affected lane token. Unknown/shared
    engine changes remain fail-safe by retaining the governance pack.
    """
    normalized = sorted({str(p).strip().replace("\\", "/").lower() for p in changed_paths if str(p).strip()})
    all_tests = discover_test_files(engine_root)
    selected: set[str] = {path for path in GOVERNANCE_TESTS if (engine_root / path).exists()}

    for original in changed_paths:
        raw = str(original).strip().replace("\\", "/")
        if not raw:
            continue
        if raw.startswith("artifacts/wow-engine/"):
            rel = raw.removeprefix("artifacts/wow-engine/")
            if rel in all_tests:
                selected.add(rel)

    lane_tokens: set[str] = set()
    for lower in normalized:
        for tokens in LANE_TOKENS.values():
            if any(token in lower for token in tokens):
                lane_tokens.update(tokens)

    if lane_tokens:
        for test in all_tests:
            lower_test = test.lower()
            if any(token in lower_test for token in lane_tokens):
                selected.add(test)

    return sorted(selected)


def _run_pytest(nodeids: Sequence[str], cwd: Path) -> int:
    if not nodeids:
        print("No pytest targets selected; refusing to infer success from an empty reproduction.", file=sys.stderr)
        return 2
    cmd = [sys.executable, "-m", "pytest", "-q", "--tb=short", *nodeids]
    print("+", " ".join(cmd))
    return subprocess.call(cmd, cwd=cwd)


def reproduce_incident(incident: str, manifest: Path, cwd: Path) -> int:
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    incidents = payload.get("incidents", {})
    entry = incidents.get(incident)
    if not isinstance(entry, dict):
        available = ", ".join(sorted(incidents)) or "none"
        raise SystemExit(f"Unknown incident fixture {incident!r}; available: {available}")
    targets = entry.get("pytest", [])
    if not isinstance(targets, list) or not all(isinstance(item, str) for item in targets):
        raise SystemExit(f"Incident {incident!r} has an invalid pytest target list")
    missing = [target.split("::", 1)[0] for target in targets if not (cwd / target.split("::", 1)[0]).exists()]
    if missing:
        raise SystemExit(f"Incident {incident!r} references missing test files: {sorted(set(missing))}")
    print(json.dumps({"incident": incident, "description": entry.get("description"), "pytest": targets}, indent=2))
    return _run_pytest(targets, cwd)


def _get_json(url: str, timeout: float) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "wow-v17-rapid-verifier/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise RuntimeError(f"{url} returned HTTP {response.status}")
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"verification request failed for {url}: {exc}") from exc


def verify_production(base_url: str, timeout: float = 20.0) -> dict:
    """Read-only verification of V17 liveness and governance invariants."""
    base = base_url.rstrip("/")
    live = _get_json(f"{base}/health/live", timeout)
    governance = _get_json(f"{base}/governance", timeout)

    if governance.get("can_execute") is not False:
        raise RuntimeError("governance invariant failed: can_execute must be false")
    terminal = governance.get("terminal_authority")
    if terminal is not None and terminal != TERMINAL_AUTHORITY:
        raise RuntimeError(f"governance invariant failed: terminal_authority={terminal!r}")
    generation = governance.get("runtime_generation")
    if generation is not None and generation != RUNTIME_GENERATION:
        raise RuntimeError(f"governance invariant failed: runtime_generation={generation!r}")

    return {
        "status": "PRODUCTION_READ_ONLY_VERIFIED",
        "health_live": live,
        "governance": governance,
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


def _write_lines(lines: Sequence[str], output: str | None) -> None:
    text = "\n".join(lines) + ("\n" if lines else "")
    if output:
        Path(output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    shard = sub.add_parser("shard-files", help="Deterministically shard pytest files")
    shard.add_argument("--root", default=".")
    shard.add_argument("--index", type=int, required=True)
    shard.add_argument("--total", type=int, required=True)
    shard.add_argument("--output")

    select = sub.add_parser("select", help="Select affected V17 tests from a git diff")
    select.add_argument("--base", required=True)
    select.add_argument("--head", default="HEAD")
    select.add_argument("--repo-root", default=str(ENGINE_ROOT.parents[1]))
    select.add_argument("--output")

    repro = sub.add_parser("repro", help="Replay a named sanitized regression fixture")
    repro.add_argument("incident")
    repro.add_argument("--manifest", default=str(ENGINE_ROOT / "tests/fixtures/incidents/manifest.json"))

    verify = sub.add_parser("verify-production", help="Read-only V17 health/governance verification")
    verify.add_argument("--base-url", default=os.getenv("WOW_BASE_URL", "https://wow-governed-probability-engine.onrender.com"))
    verify.add_argument("--timeout", type=float, default=20.0)

    args = parser.parse_args()
    if args.command == "shard-files":
        files = discover_test_files(Path(args.root))
        _write_lines(shard_test_files(files, args.index, args.total), args.output)
        return 0
    if args.command == "select":
        repo_root = Path(args.repo_root).resolve()
        changed = _changed_files(args.base, args.head, repo_root)
        tests = select_affected_tests(changed)
        _write_lines(tests, args.output)
        return 0
    if args.command == "repro":
        return reproduce_incident(args.incident, Path(args.manifest), ENGINE_ROOT)
    if args.command == "verify-production":
        print(json.dumps(verify_production(args.base_url, args.timeout), indent=2, sort_keys=True))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
