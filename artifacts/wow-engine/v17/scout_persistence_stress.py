"""Bounded synthetic stress gate for Scout persistence.

The default simulation exercises the exact Scout request slicer/reconciliation shape
at configurable multiples of a representative peak. Live HTTP mode is intentionally
staging-only: the known WOW production Supabase host is rejected and an explicit
allowed host must be supplied via WOW_SCOUT_STRESS_ALLOWED_HOST.

Research/engineering only. Never writes sporting probabilities and never grants
execution authority.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from v17.scout_brain_edge_sync import evidence_list, requests_for

PRODUCTION_HOSTS = {
    "iczfhsmjrrafhvcpmqhr.supabase.co",
    "wow-governed-probability-engine.onrender.com",
}
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"


@dataclass(frozen=True)
class StressResult:
    requests: int
    successes: int
    errors: int
    p50_ms: float
    p95_ms: float
    max_ms: float
    evidence_rows: int
    candidates_finalized: int
    concurrency: int
    multiplier: int

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.__dict__,
            "error_rate": (self.errors / self.requests) if self.requests else 0.0,
            "can_execute": False,
            "terminal_authority": TERMINAL_AUTHORITY,
        }


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return ordered[index]


def _validate_staging_url(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not host:
        raise ValueError("STRESS_URL_INVALID")
    if host in PRODUCTION_HOSTS:
        raise ValueError("PRODUCTION_STRESS_TARGET_FORBIDDEN")
    allowed = str(os.environ.get("WOW_SCOUT_STRESS_ALLOWED_HOST") or "").strip().lower()
    if host not in {"localhost", "127.0.0.1"} and host != allowed:
        raise ValueError("STRESS_HOST_NOT_EXPLICITLY_ALLOWED")


def _http_transport(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    _validate_staging_url(url)
    token = str(os.environ.get("WOW_SCOUT_STRESS_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("WOW_SCOUT_STRESS_TOKEN_UNCONFIGURED")
    req = Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-WOW-Stress-Test": "isolated-staging-only",
        },
        method="POST",
    )
    with urlopen(req, timeout=30) as response:
        body = json.loads(response.read().decode("utf-8"))
    if not isinstance(body, dict) or body.get("can_execute") is not False:
        raise RuntimeError("STRESS_RESPONSE_GOVERNANCE_INVALID")
    return body


def _simulation_transport(_: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "can_execute": False}


def build_payloads(
    rows: list[dict[str, Any]],
    *,
    multiplier: int,
    max_evidence_rows: int,
    max_bytes: int,
) -> list[dict[str, Any]]:
    if multiplier < 1:
        raise ValueError("multiplier must be >= 1")
    batches = list(requests_for(rows, max_evidence_rows=max_evidence_rows, max_bytes=max_bytes))
    payloads: list[dict[str, Any]] = []
    for iteration in range(multiplier):
        for batch_index, batch in enumerate(batches):
            payloads.append(
                {
                    "persist_phase": "APPEND",
                    "stress_iteration": iteration,
                    "stress_batch": batch_index,
                    "candidates": batch,
                    "can_execute": False,
                    "terminal_authority": TERMINAL_AUTHORITY,
                }
            )
    return payloads


def run_stress(
    payloads: list[dict[str, Any]],
    *,
    concurrency: int,
    multiplier: int,
    transport: Callable[[dict[str, Any]], dict[str, Any]],
) -> StressResult:
    if concurrency < 1:
        raise ValueError("concurrency must be >= 1")
    timings: list[float] = []
    errors = 0
    successes = 0

    def one(payload: dict[str, Any]) -> tuple[bool, float]:
        start = time.perf_counter()
        try:
            result = transport(payload)
            ok = isinstance(result, dict) and result.get("can_execute") is False
            return ok, (time.perf_counter() - start) * 1000.0
        except Exception:
            return False, (time.perf_counter() - start) * 1000.0

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(one, payload) for payload in payloads]
        for future in concurrent.futures.as_completed(futures):
            ok, elapsed = future.result()
            timings.append(elapsed)
            if ok:
                successes += 1
            else:
                errors += 1

    evidence_rows = sum(
        len(evidence_list(entry))
        for payload in payloads
        for entry in payload.get("candidates") or []
    )
    candidates_finalized = sum(
        1
        for payload in payloads
        for entry in payload.get("candidates") or []
        if (entry.get("evidence_slice") or {}).get("final") is True
    )
    return StressResult(
        requests=len(payloads),
        successes=successes,
        errors=errors,
        p50_ms=statistics.median(timings) if timings else 0.0,
        p95_ms=_percentile(timings, 0.95),
        max_ms=max(timings) if timings else 0.0,
        evidence_rows=evidence_rows,
        candidates_finalized=candidates_finalized,
        concurrency=concurrency,
        multiplier=multiplier,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="JSON array of candidate rows")
    parser.add_argument("--multiplier", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--max-evidence-rows", type=int, default=150)
    parser.add_argument("--max-bytes", type=int, default=256 * 1024)
    parser.add_argument("--staging-url", default="")
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    parser.add_argument("--max-p95-ms", type=float, default=5000.0)
    parser.add_argument("--output")
    args = parser.parse_args()

    rows = json.loads(Path(args.input).read_text())
    if not isinstance(rows, list):
        raise SystemExit("stress input must be a JSON array")
    payloads = build_payloads(
        rows,
        multiplier=args.multiplier,
        max_evidence_rows=args.max_evidence_rows,
        max_bytes=args.max_bytes,
    )
    if args.staging_url:
        _validate_staging_url(args.staging_url)
        transport = lambda payload: _http_transport(args.staging_url, payload)
        mode = "STAGING_HTTP"
    else:
        transport = _simulation_transport
        mode = "ISOLATED_SIMULATION"

    result = run_stress(
        payloads,
        concurrency=args.concurrency,
        multiplier=args.multiplier,
        transport=transport,
    ).as_dict()
    result["mode"] = mode
    result["pass"] = (
        result["error_rate"] <= args.max_error_rate
        and result["p95_ms"] <= args.max_p95_ms
        and result["successes"] == result["requests"]
    )
    result["production_soak_replaced"] = False
    result["production_canary_required"] = True

    payload = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(payload + "\n")
    print(payload)
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
