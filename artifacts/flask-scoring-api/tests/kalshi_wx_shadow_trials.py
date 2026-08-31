#!/usr/bin/env python3
"""
tests/kalshi_wx_shadow_trials.py
WOW-PATCH-2026-08-08-MULTI-AGENT-KALSHI-WX-SHADOW — 25 controlled shadow trials

CRITICAL: KALSHI_WX_SHADOW_AGENT_ENABLED must be set in os.environ BEFORE any
shadow module is imported, because kalshi_wx_shadow_agent.py evaluates the flag
at module-level.  This file sets it on the first executable line.

Runs 25 snapshot-controlled Kalshi Weather shadow research trials using real
Anthropic API calls via the legacy_platform AI integrations proxy.  Calls
run_shadow_orchestrator() directly with frozen build_test_snapshot() instances.
Does NOT go through the HTTP route.  Does NOT make new weather fetches.

SAFETY INVARIANTS (all preserved throughout)
  - CAN_EXECUTE / PRODUCTION_AUTHORITY / USER_OUTPUT_AUTHORITY remain False
    (enforced by CapabilityBoundary and KalshiWxShadowResearchClient constants).
  - advisory_only=True validated on every SHADOW_PASS result via payload intercept.
  - No production ledger writes (ShadowLedger is in-memory only).
  - No deterministic weather logic invoked.
  - Flag disabled at process exit (try/finally).

ACCEPTANCE CRITERIA
  - Schema pass rate >= 80 %
  - advisory_only=True on every passed result
  - All recommended_ceiling values in CEILING_CAPABLE_LABELS
  - No capability-boundary hook violations (hook_violations_count = 0)
  - No exception escaping the orchestrator
"""
from __future__ import annotations

# ── MUST be first: set flag before any shadow module import ───────────────────
import os
os.environ["KALSHI_WX_SHADOW_AGENT_ENABLED"] = "true"

import sys

# ── Path setup ─────────────────────────────────────────────────────────────────
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from unittest.mock import patch

# ── Shadow module imports (flag is now True in environment) ────────────────────
from gate_engine.kalshi_wx_shadow_snapshot import build_test_snapshot
from gate_engine.kalshi_wx_shadow_orchestrator import (
    run_shadow_orchestrator,
    _build_sdk_client as _orch_build_sdk,
)
from gate_engine.kalshi_wx_shadow_capability_boundary import CapabilityBoundary
from gate_engine.kalshi_wx_shadow_ledger import ShadowLedger
from gate_engine.kalshi_wx_shadow_registry import CEILING_CAPABLE_LABELS
from gate_engine.kalshi_wx_shadow_schema import validate_shadow_output

# ── Patch the module-level constant directly in case module was pre-cached ─────
import gate_engine.kalshi_wx_shadow_agent as _agent_mod
_agent_mod.KALSHI_WX_SHADOW_AGENT_ENABLED = True

# ── SDK client ─────────────────────────────────────────────────────────────────
_sdk_client = _orch_build_sdk()
if _sdk_client is None:
    print("ERROR: Cannot build Anthropic SDK client — no API key available.")
    sys.exit(1)

# ── Payload interceptor ────────────────────────────────────────────────────────
# The orchestrator does: from gate_engine.kalshi_wx_shadow_schema import validate_shadow_output
# so the local name in the orchestrator module must be patched there.
_VALIDATE_PATCH_TARGET = "gate_engine.kalshi_wx_shadow_orchestrator.validate_shadow_output"

def _make_interceptor(payloads: list, real_fn):
    """Returns a validate_shadow_output replacement that records the payload."""
    def _intercept(payload):
        if isinstance(payload, dict):
            payloads.append(payload.copy())
        return real_fn(payload)
    return _intercept


# ── Trial snapshot definitions — 25 scenarios ─────────────────────────────────

def _snap(label: str, **kw):
    city      = kw.pop("city",             "New York")
    station   = kw.pop("station",          "KNYC")
    date      = kw.pop("date",             "2026-08-15")
    fh        = kw.pop("forecast_high",    84.0)
    tier      = kw.pop("tier",             "nws_primary")
    horizon   = kw.pop("horizon_hours",    24.0)
    sigma_f   = kw.pop("sigma_f",          3.5)
    src_fail  = kw.pop("source_failures",  ())
    readiness = "READY" if fh is not None else "DATA_UNAVAILABLE"
    return build_test_snapshot(
        research_snapshot_id              = f"trial-{uuid.uuid4()}",
        canonical_event_id                = f"trial-{label}",
        city                              = city,
        station                           = station,
        market_date                       = date,
        source_cutoff_timestamp           = "2026-08-14T18:00:00Z",
        forecast_high_used_by_deterministic_model = float(fh) if fh is not None else 0.0,
        weather_data_source_tier          = tier,
        forecast_horizon_hours            = float(horizon),
        sigma_f                           = float(sigma_f),
        deterministic_weather_readiness_state = readiness,
        source_failures                   = src_fail,
        source_disagreements              = (),
        source_timestamps                 = {tier: "2026-08-14T17:55:00Z"},
        source_provenance                 = {},
    )


TRIALS = [
    # ── A: Five cities, NWS primary, typical summer temps ────────────────────
    _snap("A1-nyc-nws",   city="New York",    station="KNYC", forecast_high=88.0,
          sigma_f=3.5, horizon_hours=24.0, tier="nws_primary"),
    _snap("A2-la-nws",    city="Los Angeles", station="KLAX", forecast_high=82.0,
          sigma_f=2.5, horizon_hours=18.0, tier="nws_primary"),
    _snap("A3-chi-nws",   city="Chicago",     station="KMDW", forecast_high=79.0,
          sigma_f=4.0, horizon_hours=36.0, tier="nws_primary"),
    _snap("A4-mia-nws",   city="Miami",       station="KMIA", forecast_high=91.0,
          sigma_f=3.0, horizon_hours=12.0, tier="nws_primary"),
    _snap("A5-aus-nws",   city="Austin",      station="KAUS", forecast_high=97.0,
          sigma_f=3.5, horizon_hours=30.0, tier="nws_primary"),

    # ── B: Open-Meteo fallback (NWS failed) ──────────────────────────────────
    _snap("B1-nyc-om",    city="New York",    station="KNYC", forecast_high=86.0,
          sigma_f=4.0, horizon_hours=24.0, tier="open_meteo_fallback",
          source_failures=("nws: HTTP 503",)),
    _snap("B2-la-om",     city="Los Angeles", station="KLAX", forecast_high=80.0,
          sigma_f=3.0, horizon_hours=18.0, tier="open_meteo_fallback",
          source_failures=("nws: timeout",)),
    _snap("B3-chi-om",    city="Chicago",     station="KMDW", forecast_high=76.0,
          sigma_f=4.5, horizon_hours=48.0, tier="open_meteo_fallback",
          source_failures=("nws: gridpoint_not_found",)),
    _snap("B4-mia-om",    city="Miami",       station="KMIA", forecast_high=89.0,
          sigma_f=3.5, horizon_hours=36.0, tier="open_meteo_fallback",
          source_failures=("nws: rate_limit",)),
    _snap("B5-aus-om",    city="Austin",      station="KAUS", forecast_high=95.0,
          sigma_f=4.0, horizon_hours=24.0, tier="open_meteo_fallback",
          source_failures=("nws: HTTP 500",)),

    # ── C: Varying sigma_f ────────────────────────────────────────────────────
    _snap("C1-sig-1.5",   city="New York",    station="KNYC", forecast_high=85.0,
          sigma_f=1.5, horizon_hours=6.0),
    _snap("C2-sig-2.5",   city="Chicago",     station="KMDW", forecast_high=78.0,
          sigma_f=2.5, horizon_hours=12.0),
    _snap("C3-sig-5.0",   city="Miami",       station="KMIA", forecast_high=90.0,
          sigma_f=5.0, horizon_hours=48.0),
    _snap("C4-sig-7.0",   city="Los Angeles", station="KLAX", forecast_high=81.0,
          sigma_f=7.0, horizon_hours=72.0, tier="open_meteo_fallback",
          source_failures=("nws: timeout",)),
    _snap("C5-sig-10.0",  city="Austin",      station="KAUS", forecast_high=93.0,
          sigma_f=10.0, horizon_hours=120.0, tier="open_meteo_fallback",
          source_failures=("nws: timeout", "open_meteo: partial")),

    # ── D: Varying forecast horizons ──────────────────────────────────────────
    _snap("D1-h6",        city="New York",    station="KNYC", forecast_high=87.0,
          sigma_f=3.5, horizon_hours=6.0),
    _snap("D2-h18",       city="Chicago",     station="KMDW", forecast_high=80.0,
          sigma_f=3.5, horizon_hours=18.0),
    _snap("D3-h36",       city="Miami",       station="KMIA", forecast_high=88.0,
          sigma_f=4.0, horizon_hours=36.0),
    _snap("D4-h72",       city="Los Angeles", station="KLAX", forecast_high=79.0,
          sigma_f=5.0, horizon_hours=72.0, tier="open_meteo_fallback",
          source_failures=("nws: HTTP 503",)),
    _snap("D5-h120",      city="Austin",      station="KAUS", forecast_high=92.0,
          sigma_f=6.5, horizon_hours=120.0, tier="open_meteo_fallback",
          source_failures=("nws: timeout", "open_meteo: HTTP 503")),

    # ── E: Multi-tier failures and edge cases ─────────────────────────────────
    _snap("E1-2fail",     city="Chicago",     station="KMDW", forecast_high=77.0,
          sigma_f=5.0, horizon_hours=48.0, tier="noaa_ncei_fallback",
          source_failures=("nws: HTTP 503", "open_meteo: rate_limit")),
    _snap("E2-allok-lo",  city="New York",    station="KNYC", forecast_high=65.0,
          sigma_f=3.0, horizon_hours=12.0),
    _snap("E3-allok-hi",  city="Miami",       station="KMIA", forecast_high=99.0,
          sigma_f=2.0, horizon_hours=8.0),
    _snap("E4-noaa",      city="Austin",      station="KAUS", forecast_high=94.0,
          sigma_f=4.5, horizon_hours=24.0, tier="noaa_ncei_fallback",
          source_failures=("nws: HTTP 503", "open_meteo: timeout")),
    _snap("E5-none-fail", city="Los Angeles", station="KLAX", forecast_high=83.0,
          sigma_f=3.5, horizon_hours=20.0),
]

assert len(TRIALS) == 25, f"Expected 25 trials, got {len(TRIALS)}"

_MIN_PASS_RATE = 0.80


# ── Run trials ─────────────────────────────────────────────────────────────────

def run_trials() -> list[dict]:
    results  = []
    ledger   = ShadowLedger()
    boundary = CapabilityBoundary()

    print(f"\n{'='*72}")
    print(f"Kalshi Weather Shadow Pilot — 25 Controlled Trials")
    print(f"Started : {datetime.now(timezone.utc).isoformat()}")
    print(f"Flag    : KALSHI_WX_SHADOW_AGENT_ENABLED=true (this process only)")
    print(f"{'='*72}\n")
    print(f"{'#':>3}  {'Label':<28} {'R'} {'Outcome':<11} {'Status':<12} "
          f"{'Ceiling':<30} {'t(s)':>5}")
    print(f"{'-'*3}  {'-'*28} {'-'} {'-'*11} {'-'*12} {'-'*30} {'-'*5}")

    for i, snap in enumerate(TRIALS, 1):
        label     = snap.canonical_event_id.replace("trial-", "")
        payloads: list[dict] = []
        exc_str   = None
        svr       = None
        t0        = time.monotonic()

        # Intercept validate_shadow_output on the orchestrator's local name
        interceptor = _make_interceptor(payloads, validate_shadow_output)
        try:
            with patch(_VALIDATE_PATCH_TARGET, side_effect=interceptor):
                svr = run_shadow_orchestrator(
                    city=snap.city,
                    date=snap.market_date,
                    run_id=snap.research_snapshot_id,
                    sdk_client=_sdk_client,
                    capability_boundary=boundary,
                    ledger=ledger,
                    snapshot=snap,
                )
        except Exception as exc:
            exc_str = f"{type(exc).__name__}: {exc}"

        elapsed = time.monotonic() - t0

        # Ledger entry for this run
        recent = ledger.get_recent(1)
        entry  = recent[0] if recent else None

        # Payload fields from intercepted dict
        payload     = payloads[-1] if payloads else {}
        ceiling     = payload.get("recommended_ceiling")
        advisory    = payload.get("advisory_only")
        status      = entry.status if entry else ("EXCEPTION" if exc_str else "UNKNOWN")
        hook_viols  = entry.hook_violations_count if entry else 0
        sub_ok      = list(entry.subagents_succeeded)  if entry else []
        sub_fail    = list(entry.subagents_failed)      if entry else []

        if exc_str:
            outcome = "EXCEPTION"
            passed  = False
            viol    = None
        else:
            passed  = (svr.passed if svr else False)
            viol    = (svr.violation.name if (svr and svr.violation) else None)
            outcome = ("SHADOW_PASS" if passed
                       else ("BLOCKED"     if status == "BLOCKED"
                             else "SCHEMA_FAIL"))

        row = {
            "trial":         i,
            "label":         label,
            "city":          snap.city,
            "sigma_f":       snap.sigma_f,
            "horizon_hours": snap.forecast_horizon_hours,
            "tier":          snap.weather_data_source_tier,
            "n_src_fail":    len(snap.source_failures),
            "elapsed_s":     round(elapsed, 2),
            "outcome":       outcome,
            "passed":        passed,
            "status":        status,
            "ceiling":       ceiling,
            "advisory_only": advisory,
            "violation":     viol,
            "hook_violations": hook_viols,
            "subagents_ok":  sub_ok,
            "subagents_fail":sub_fail,
            "exception":     exc_str,
        }
        results.append(row)

        flag     = "✓" if passed else ("!" if exc_str else "✗")
        ceil_str = (ceiling or "—")[:30]
        print(
            f"{i:>3}. {label:<28} {flag} {outcome:<11} {status:<12} "
            f"{ceil_str:<30} {elapsed:>5.1f}"
        )

    print()
    return results


# ── Report ─────────────────────────────────────────────────────────────────────

def _ok(b: bool) -> str:
    return "PASS" if b else "FAIL"


def report(results: list[dict]) -> bool:
    n         = len(results)
    n_pass    = sum(1 for r in results if r["passed"])
    n_fail    = sum(1 for r in results if not r["passed"] and r["outcome"] == "SCHEMA_FAIL")
    n_blocked = sum(1 for r in results if r["outcome"] == "BLOCKED")
    n_except  = sum(1 for r in results if r["outcome"] == "EXCEPTION")
    pass_rate = n_pass / n

    advisory_violations  = [r for r in results if r["passed"] and r["advisory_only"] is not True]
    ceiling_violations   = [r for r in results
                             if r["passed"] and r["ceiling"] is not None
                             and r["ceiling"] not in CEILING_CAPABLE_LABELS]
    hook_viols_total     = sum(r["hook_violations"] for r in results)

    ceil_dist  = Counter(r["ceiling"] for r in results if r["passed"] and r["ceiling"])
    viol_dist  = Counter(r["violation"] for r in results if r["violation"])
    fail_dist  = Counter(sid for r in results for sid in r["subagents_fail"])

    print(f"{'='*72}")
    print("TRIAL RESULTS SUMMARY")
    print(f"{'='*72}")
    print(f"  Total trials       : {n}")
    print(f"  SHADOW_PASS        : {n_pass}  ({pass_rate*100:.1f}%)")
    print(f"  SCHEMA_FAIL        : {n_fail}")
    print(f"  BLOCKED            : {n_blocked}")
    print(f"  EXCEPTION          : {n_except}")
    print(f"  Hook violations    : {hook_viols_total}")
    print()

    crit1 = pass_rate >= _MIN_PASS_RATE
    crit2 = len(advisory_violations) == 0
    crit3 = len(ceiling_violations)  == 0
    crit4 = n_except == 0
    crit5 = hook_viols_total == 0

    print("ACCEPTANCE CRITERIA")
    print(f"  [{_ok(crit1)}] Pass rate >= {_MIN_PASS_RATE*100:.0f}%"
          f"                actual: {pass_rate*100:.1f}%")
    print(f"  [{_ok(crit2)}] advisory_only=True on all passed"
          f"  violations: {len(advisory_violations)}")
    print(f"  [{_ok(crit3)}] All ceilings in CEILING_CAPABLE_LABELS"
          f"  violations: {len(ceiling_violations)}")
    print(f"  [{_ok(crit4)}] No exceptions from orchestrator"
          f"   exceptions: {n_except}")
    print(f"  [{_ok(crit5)}] No hook/authority violations"
          f"    hook_violations: {hook_viols_total}")

    if ceil_dist:
        print(f"\n  Ceiling distribution (passed runs):")
        for c, cnt in sorted(ceil_dist.items(), key=lambda x: -x[1]):
            print(f"    {c:<42} {cnt:>3}")

    if viol_dist:
        print(f"\n  Schema-fail reasons:")
        for v, cnt in sorted(viol_dist.items(), key=lambda x: -x[1]):
            print(f"    {v:<42} {cnt:>3}")

    if fail_dist:
        print(f"\n  Failed subagents across all runs:")
        for sid, cnt in sorted(fail_dist.items(), key=lambda x: -x[1]):
            print(f"    {sid:<42} {cnt:>3}")

    if ceiling_violations:
        print(f"\n  CEILING VIOLATIONS:")
        for r in ceiling_violations:
            print(f"    Trial {r['trial']} {r['label']}: {r['ceiling']!r}")

    if advisory_violations:
        print(f"\n  ADVISORY_ONLY VIOLATIONS:")
        for r in advisory_violations:
            print(f"    Trial {r['trial']} {r['label']}: advisory_only={r['advisory_only']!r}")

    if n_except:
        print(f"\n  EXCEPTIONS:")
        for r in results:
            if r["exception"]:
                print(f"    Trial {r['trial']} {r['label']}: {r['exception']}")

    ok = crit1 and crit2 and crit3 and crit4 and crit5
    print(f"\n{'='*72}")
    print(f"PILOT VERDICT : {'ACCEPTED' if ok else 'NOT ACCEPTED'}")
    print(f"Completed     : {datetime.now(timezone.utc).isoformat()}")
    print(f"{'='*72}\n")
    return ok


if __name__ == "__main__":
    try:
        results = run_trials()
        ok      = report(results)
    finally:
        # Disable flag — this process-only env var modification ends here anyway,
        # but be explicit for clarity.
        os.environ.pop("KALSHI_WX_SHADOW_AGENT_ENABLED", None)
        print("Flag KALSHI_WX_SHADOW_AGENT_ENABLED disabled.")

    sys.exit(0 if ok else 1)
