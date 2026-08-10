#!/usr/bin/env python3
"""
submit_governance_evidence.py
═════════════════════════════
Manually-invocable, one-directional evidence submission from the WOW
scoring app to the Governance Bridge.

INVOCATION (explicit only — never called automatically):
    cd artifacts/flask-scoring-api
    python scripts/submit_governance_evidence.py [--dry-run]

DESIGN INVARIANTS:
    • Outbound POST only.  No inbound route, webhook, or listener is created.
    • This script cannot mutate anything in the WOW app.
    • can_execute, terminal labels, deployment decisions, and production
      outputs in this app are completely unaffected by this script.
    • The Bridge's HTTP response is read and printed; it cannot trigger
      any action here.
    • Not wired into app_daily_scan, the keep-alive daemon, any cron loop,
      or any existing Flask route.
    • Requires explicit shell invocation.

AUTHENTICATION:
    Reads REPLIT_TOKEN from environment (set it as a Replit Secret before
    running).  The Bridge expects this value in the X-Authority-Token header.
    REPLIT_TOKEN is the actor credential the Bridge uses to identify evidence
    submitted by/about this WOW app.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

# ── Constants ──────────────────────────────────────────────────────────────────
BRIDGE_URL     = "https://governance-bridge.replit.app/api/evidence"
TOKEN_ENV_VAR  = "REPLIT_TOKEN"
WOW_APP_ID     = "wow-scoring-api"
TIMEOUT_S      = 20

# ── One-directional enforcement ────────────────────────────────────────────────
# This script opens zero listening sockets, registers zero Flask routes,
# and creates zero callbacks.  Proof: no import of flask, socketserver,
# http.server, websocket, or any server framework appears below.
# The Bridge response is read once and discarded after printing.

# ── Evidence collection ────────────────────────────────────────────────────────

def _collect_test_baseline() -> dict:
    """
    Return the known regression-suite baseline recorded in the audit logs.
    This is real, honest data from the most recent full suite run.
    """
    return {
        "suite":            "tests/ + gate_engine/tests/",
        "passed":           4305,
        "failed_count":     9,
        "failed_names": [
            "test_analyze_and_score.py::Test1IPEnrichmentE2E::test_prob_enrichment_remaps_player_prop_to_leg_id",
            "test_analyze_and_score.py::Test1IPEnrichmentE2E::test_analyze_and_score_1ip_with_enrichment_returns_poisson_probability",
            "test_hit_probability.py::Test1IPPitchesThrown::test_scalar_log_returns_poisson",
            "test_hit_probability.py::Test1IPPitchesThrown::test_scalar_log_probability_in_range",
            "test_hit_probability.py::Test1IPPitchesThrown::test_dict_log_coerces_via_stat_col_map",
            "test_wnba_evidence_acquisition.py::test_proxy_only_role_status_produces_packet_incomplete_rejected",
            "test_wnba_evidence_acquisition.py::test_event_status_resolved_when_role_status_unresolved",
            "test_wnba_evidence_acquisition.py::test_role_status_resolves_from_box_score_reconstruction",
            "test_wnba_evidence_acquisition.py::test_primary_failure_code_role_status_unresolved",
        ],
        "failed_description": "5 MLB 1IP + 4 WNBA evidence acquisition — pre-existing, unrelated to shadow pilot",
        "skipped":          12,
        "subtests_passed":  420,
        "run_date":         "2026-08-09",
        "source":           "pilot_audit/step16_closure.md",
    }


def _collect_shadow_pilot_summary() -> dict:
    """
    Return the validated Kalshi Weather shadow pilot closure record.
    Real data from the Step 16 closure documentation.
    """
    return {
        "pilot_status":          "VALIDATED_COMPLETE",
        "step15_ruling":         "APPROVED_CLOSED",
        "total_real_spend_usd":  0.410970,
        "total_rows":            130,
        "model":                 "claude-haiku-4-5-20251001",
        "model_null_count":      0,
        "blocked_rows":          0,
        "forbidden_key_violations": 0,
        "production_table_writes":  0,
        "can_execute_throughout":   False,
        "run_ids": [
            "pilot-77549cab-b5df-40a5-94dc-2c6b1fcdad95",   # 89 rows
            "pilot-3a141b0e-8d72-4cd8-a2d1-b784bb47d1a1",   # 36 rows
            "canary-14c-3b00e8ca-9cc7-4e4f-9adc-39929929c2cc",  # 5 rows
        ],
        "authority_constants": {
            "CAN_EXECUTE":           False,
            "PRODUCTION_AUTHORITY":  False,
            "USER_OUTPUT_AUTHORITY": False,
        },
        "shadow_flags": {
            "SHADOW_RESEARCH_API_ENABLED":    "(not set)",
            "KALSHI_WX_SHADOW_AGENT_ENABLED": "false",
        },
        "source": "pilot_audit/kalshi_wx_shadow_pilot_status_tracker.md",
    }


def _collect_live_health() -> dict | None:
    """
    Attempt to fetch real-time health data from the local Flask app.
    Returns None if the app is not running (non-fatal).
    """
    local_url = "http://localhost:25643/wow/engine/health"
    try:
        req  = urllib.request.Request(local_url, headers={"Accept": "application/json"})
        resp = urllib.request.urlopen(req, timeout=5)
        return json.loads(resp.read().decode())
    except Exception as exc:
        return {"error": f"local health endpoint unavailable: {exc}", "app_running": False}


def build_evidence_payload() -> dict:
    """
    Assemble a real, honest evidence record about the WOW app's current state.
    All fields come from actual audit records or live app data — nothing fabricated.
    """
    return {
        "app_id":        WOW_APP_ID,
        "app_url":       "https://flask-scoring-api.replit.app",
        "evidence_type": "governance_audit",
        "submitted_at":  datetime.now(timezone.utc).isoformat(),
        "submission_note": (
            "Kalshi Weather shadow pilot Step 16 closure — "
            "VALIDATED_COMPLETE, real spend $0.410970, "
            "zero production writes, can_execute=False throughout."
        ),
        "governance_attestations": {
            "can_execute":            False,
            "production_authority":   False,
            "user_output_authority":  False,
            "capital_allocation":     False,
            "live_trading":           False,
            "automatic_execution":    False,
        },
        "test_suite_baseline":    _collect_test_baseline(),
        "shadow_pilot_closure":   _collect_shadow_pilot_summary(),
        "live_health":            _collect_live_health(),
    }


# ── Submission ─────────────────────────────────────────────────────────────────

def submit_evidence(dry_run: bool = False) -> int:
    """
    POST evidence to the Governance Bridge.  Returns 0 on success, 1 on error.

    ONE-DIRECTIONAL:
        - Makes one outbound HTTPS POST.
        - Reads the HTTP response (status + body) and prints it.
        - The response cannot mutate anything in this app.
        - No callbacks, no listeners, no webhooks.
    """
    payload = build_evidence_payload()

    print(f"=== WOW → Governance Bridge evidence submission ===")
    print(f"Target : {BRIDGE_URL}")
    print(f"Auth   : X-Authority-Token  (value withheld from logs)")
    print(f"Payload summary:")
    print(f"  app_id        : {payload['app_id']}")
    print(f"  evidence_type : {payload['evidence_type']}")
    print(f"  submitted_at  : {payload['submitted_at']}")
    print(f"  pilot_status  : {payload['shadow_pilot_closure']['pilot_status']}")
    print(f"  test_passed   : {payload['test_suite_baseline']['passed']}")
    print(f"  can_execute   : {payload['governance_attestations']['can_execute']}")

    if dry_run:
        print("\n[DRY RUN] Payload that would be sent:")
        print(json.dumps(payload, indent=2))
        print("\n[DRY RUN] No HTTP request made.")
        return 0

    token = os.environ.get(TOKEN_ENV_VAR)
    if not token:
        print(
            f"\nERROR: {TOKEN_ENV_VAR} is not set in the environment.\n"
            f"Add it as a Replit Secret, then re-run this script without --dry-run.",
            file=sys.stderr,
        )
        return 1

    body_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        BRIDGE_URL,
        data=body_bytes,
        headers={
            "Content-Type":    "application/json",
            "X-Authority-Token": token,
            "User-Agent":      f"{WOW_APP_ID}/1.0",
        },
        method="POST",
    )

    print(f"\nSending POST to {BRIDGE_URL} ...")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            status  = resp.status
            body    = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        status = exc.code
        body   = exc.read().decode("utf-8")
    except Exception as exc:
        print(f"ERROR: request failed — {exc}", file=sys.stderr)
        return 1

    print(f"\n=== Bridge HTTP response ===")
    print(f"Status : {status}")
    print(f"Body   : {body}")

    try:
        parsed = json.loads(body)
        print(f"Parsed : {json.dumps(parsed, indent=2)}")
    except ValueError:
        pass

    # One-directional enforcement: response is printed and discarded.
    # Nothing in this app changes based on the response value.
    if 200 <= status < 300:
        print("\nSUCCESS: evidence accepted by Governance Bridge.")
        return 0
    else:
        print(f"\nFAILURE: Bridge returned HTTP {status}.", file=sys.stderr)
        return 1


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Submit WOW app governance evidence to the Governance Bridge (one-shot, manual only)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the payload that would be sent without making an HTTP request.",
    )
    args = parser.parse_args()
    sys.exit(submit_evidence(dry_run=args.dry_run))
