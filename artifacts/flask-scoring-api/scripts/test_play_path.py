#!/usr/bin/env python3
"""
test_play_path.py — Task #57
Confirm a fully-supplied scoring row reaches terminal_disposition=PLAY.

Runs against the live server at localhost:25643.
Exits 0 on success, 1 on any failure.

Sequence:
  1. Fetch governance hash
  2. Seed a fresh settlement entry so the staleness gate passes
  3. Confirm settlement freshness shows stale=False
  4. Submit /gate-engine/run with a complete enrichment payload
  5. Assert all 6 PLAY-path conditions + all 10 invocation_audit fields
"""

import json
import os
import sys
import uuid
import urllib.request
import urllib.error
from datetime import date

BASE_URL = "http://localhost:25643"
API_KEY  = os.environ.get("SCORING_API_KEY", "")
TODAY    = date.today().isoformat()


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def get_public(path: str) -> dict:
    """GET without auth (public endpoints)."""
    req = urllib.request.Request(f"{BASE_URL}{path}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def get_auth(path: str) -> dict:
    """GET with API key (protected endpoints)."""
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        headers={"X-API-Key": API_KEY},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def post(path: str, body: dict) -> tuple[dict, int]:
    data = json.dumps(body).encode()
    req  = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json", "X-API-Key": API_KEY},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        body_bytes = e.read()
        try:
            return json.loads(body_bytes), e.code
        except Exception:
            return {"raw_error": body_bytes.decode("utf-8", errors="replace")}, e.code


def check(condition: bool, label: str, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    if not condition:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Step 1 — governance hash
# ---------------------------------------------------------------------------

print("=== Step 1: get governance hash ===")
gov  = get_public("/wow/governance/status")
HASH = gov["governance_hash"]
print(f"  hash: {HASH}")
print(f"  version: {gov.get('engine_code_version')}")

# ---------------------------------------------------------------------------
# Step 2 — seed a fresh settlement entry
# The settlement loopback gate requires an entry ingested within 18 hours.
# In a fresh dev environment the table is empty (correctly blocking
# FINAL_APPROVED — no calibration signal).  Seeding one entry mirrors real
# production state where prior-day slates have been settled before scoring.
# ---------------------------------------------------------------------------

print("\n=== Step 2: seed settlement ledger ===")
settle_resp, settle_status = post("/lock-api/settle", {
    "date":                      TODAY,
    "sport":                     "NBA",
    "player":                    "Test Player",
    "market":                    "Points",
    "side":                      "MORE",
    "submitted_line":            20.5,
    "closing_line":              20.5,
    "submitted_odds_or_payout":  -110,
    "closing_odds_or_projection": -112,
    "model_probability":         0.57,
    "market_probability":        0.52,
    "edge":                      0.05,
    "result":                    "WIN",
    "CLV":                       0.02,
    "failure_tag":               None,
    "dominant_failure_tag":      None,
    "slip_type":                 "SINGLE",
    "pick_count":                1,
    "payout_multiplier":         1.91,
    "slip_EV":                   0.06,
    "actual_slip_result":        "WIN",
    "notes":                     "test_play_path seed entry",
})
print(f"  HTTP status: {settle_status}")
check(settle_status == 201, "settlement entry ingested (HTTP 201)",
      f"got {settle_status}: {settle_resp}")

# ---------------------------------------------------------------------------
# Step 3 — confirm settlement freshness
# ---------------------------------------------------------------------------

print("\n=== Step 3: confirm settlement freshness ===")
fresh = get_auth("/lock-api/settle/freshness")
print(f"  stale={fresh.get('stale')}  age_hours={fresh.get('age_hours')}")
check(fresh.get("stale") is False, "settlement freshness: stale=False",
      f"got stale={fresh.get('stale')}")

# ---------------------------------------------------------------------------
# Step 4 — build and submit the fully-supplied /gate-engine/run request
# ---------------------------------------------------------------------------

PLAYER    = "Test Player"
PROP_TYPE = "Points"
LINE      = 20.5
DIRECTION = "MORE"
SPORT     = "NBA"
ENR_KEY   = f"{PLAYER.lower()}:{PROP_TYPE.lower()}"

# game_log: all 10 values above line → l10_hit_rate = 1.0 → money_qualified
# ev_gate: l10_hit(1.0) >= MIN_L10_HIT_RATE(0.55) → edge_component = 0.5
GAME_LOG   = [22.0, 25.0, 23.0, 24.0, 26.0, 21.0, 28.0, 22.0, 25.0, 24.0]
SEASON_LOG = [21.0, 23.0, 22.0, 24.0, 25.0, 20.0, 27.0, 21.0, 24.0, 23.0,
              22.0, 25.0, 21.0, 26.0, 23.0, 24.0, 22.0, 25.0, 20.0, 27.0]

# sportsbook_line == pp_line → delta = 0 → MARKET_VERIFIED
# market_gate: EDGE_THRESHOLD = 0.04; delta=0 ≤ 0.04 → MARKET_VERIFIED
SB_LINE = 20.5

enrichment_payload = {
    # ── Data contract enrichment required fields ──────────────────────────
    "opponent":                  "TestTeam B",
    "game_date":                 TODAY,
    "book_or_platform":          "FanDuel",
    "odds_or_payout":            -110,
    "data_timestamp":            f"{TODAY}T10:00:00Z",
    "status_timestamp":          f"{TODAY}T10:00:00Z",
    "role_timestamp":            f"{TODAY}T10:00:00Z",
    "l5_values":                 [24.0, 26.0, 21.0, 28.0, 22.0],
    "l10_values":                GAME_LOG,
    "l10_median":                24.0,
    "l10_mean":                  24.0,
    "l5_line_used":              LINE,
    "market_no_vig_probability": 0.52,
    # ── prob_ledger: full Stage 2 schema ─────────────────────────────────
    # Component weights must be within bounds:
    #   market_no_vig: [0.40, 0.50]  l10_distribution: [0.25, 0.35]
    #   role_usage: [0.10, 0.20]
    # final_model_prob < 0.60 avoids shrinkage requirement.
    # confidence_interval must be present when final_model_prob is set.
    "model_probability_ledger": {
        "components": [
            {"name": "market_no_vig",    "weight": 0.45, "value": 0.52, "source": "FanDuel"},
            {"name": "l10_distribution", "weight": 0.30, "value": 0.58, "source": "game_log"},
            {"name": "role_usage",       "weight": 0.15, "value": 0.55, "source": "rotowire"},
        ],
        "final_model_prob":       0.57,
        "confidence_interval":    "0.52-0.62",
        "uncertainty_haircut":    0.04,
        "usable_probability":     0.53,
        "calibration_status":     "CALIBRATED",
        "shrinkage_applied":      False,
        "shrinkage_baseline":     None,
        # Stage 2 required fields
        "raw_probability":        0.58,
        "calibrated_probability": 0.57,
        "lower_bound":            0.52,
        "upper_bound":            0.62,
        "model_timestamp":        f"{TODAY}T10:00:00Z",
        "source_snapshot_id":     "snap-test-001",
        "calibration_method":     "platt_scaling",
    },
    "payout_context":            {"american_odds": -110, "implied_prob": 0.524},
    # ── failure_path_matrix: three named paths, all non-abstract ─────────
    # PRIMARY floor ≤ 30% avoids haircut-required violation.
    "failure_path_matrix": {
        "PRIMARY_KILL_PATH": {
            "scenario":         "Line moves against position before tip-off",
            "probability_band": "10-20%",
            "model_adjustment": "Reduce edge estimate by 0.05 on adverse movement > 1.0",
            "evidence":         "Line movement > 1.0 precedes miss 18% of time in sample",
        },
        "SECONDARY_KILL_PATH": {
            "scenario":         "Player logs reduced minutes due to blowout",
            "probability_band": "8-15%",
            "model_adjustment": "Apply 10% probability haircut when blowout risk elevated",
            "evidence":         "Blowout minutes restriction observed in 12% of sample games",
        },
        "BLACK_SWAN_PATH": {
            "scenario":         "Player scratched from lineup at last minute due to injury",
            "probability_band": "2-5%",
            "model_adjustment": "Immediate void on confirmed DNP",
            "evidence":         "Late scratch rate across NBA sample 2-3% per game",
            "void_dnp_risk":    True,
        },
    },
    "directional_exposure_tags": [],
    "provisional_label":         "FINAL_APPROVED",
    "validation_status":         "PASS",
    # blocker_reason_if_blocked intentionally absent (validation_status != FAILED)
    # ── Gate inputs (not in data contract but drive gate outcomes) ────────
    "game_log":                  GAME_LOG,
    "season_log":                SEASON_LOG,
    "sportsbook_line":           SB_LINE,
    "best_available":            SB_LINE,
    "consensus_line":            SB_LINE,
    "clv_entry_price":           -110,
    "status_payload": {
        "status":              "ACTIVE",
        "source":              "Test",
        "dnp_risk":            False,
        "minutes_restriction": False,
    },
}

request_body = {
    "rows": [{
        "player":       PLAYER,
        "sport":        SPORT,
        "prop_type":    PROP_TYPE,
        "line":         LINE,
        "direction":    DIRECTION,
        "slate_date":   TODAY,
        "board_source": "PrizePicks",
        "game":         "TestTeam A vs TestTeam B",
    }],
    "target_date":              TODAY,
    "enrichment":               {ENR_KEY: enrichment_payload},
    "record_entries":           False,
    "response_mode":            "slim",
    "expected_governance_hash": HASH,
    "session_id":               f"sess-play-{uuid.uuid4().hex[:8]}",
    "research_run_id":          f"run-play-{uuid.uuid4().hex[:8]}",
    "as_of":                    f"{TODAY}T10:00:00Z",
}

print(f"\n=== Step 4: submit /gate-engine/run (slim mode) ===")
print(f"  enrichment key : {ENR_KEY!r}")
print(f"  game_log       : {GAME_LOG}")
print(f"  sportsbook_line: {SB_LINE}  pp_line: {LINE}  delta=0")

resp, http_status = post("/gate-engine/run", request_body)
print(f"  HTTP status: {http_status}")

if http_status != 200:
    print(f"\n  ERROR response:\n{json.dumps(resp, indent=2)[:2000]}")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Step 5 — assert required PLAY-path conditions
# ---------------------------------------------------------------------------

print("\n=== Step 5: assert PLAY-path conditions ===")

ia = resp.get("invocation_audit", {})

# 1. invocation_audit block present
check("invocation_audit" in resp, "invocation_audit block present")

# 2. required_runtime_evidence_complete = true
rre = ia.get("required_runtime_evidence_complete")
check(rre is True, "required_runtime_evidence_complete=true", f"got {rre!r}")

# 3. validation_status = VALID_RUNTIME_EVIDENCE
vs = resp.get("validation_status")
check(vs == "VALID_RUNTIME_EVIDENCE", "validation_status=VALID_RUNTIME_EVIDENCE", f"got {vs!r}")

# 4. terminal_disposition = PLAY
td = resp.get("terminal_disposition")
check(td == "PLAY", "terminal_disposition=PLAY", f"got {td!r}")

# 5. final_card has ≥1 entry
fc = resp.get("final_card") or []
check(len(fc) >= 1, "final_card has ≥1 entry", f"len={len(fc)}")

# 6. can_execute = false (governance invariant)
ce = resp.get("can_execute")
check(ce is False, "can_execute=false", f"got {ce!r}")

# 7. slim mode: all 10 invocation_audit fields with correct types
print("\n  invocation_audit field checks:")
EXPECTED_IA_FIELDS = {
    "manifest_hash":                      str,
    "required_skills":                    list,
    "invoked_skills":                     list,
    "missing_required_skills":            list,
    "skill_verification_status":          str,
    "ceilings_applied":                   list,
    "lowest_ceiling":                     str,
    "unique_theses":                      list,
    "duplicate_groups":                   list,
    "required_runtime_evidence_complete": bool,
}
for field, expected_type in EXPECTED_IA_FIELDS.items():
    val     = ia.get(field)
    present = field in ia
    ok      = present and isinstance(val, expected_type)
    check(
        ok,
        f"  ia.{field}",
        f"present={present} type={type(val).__name__ if present else 'MISSING'} "
        f"(expected {expected_type.__name__})",
    )

# ---------------------------------------------------------------------------
# Step 6 — summary snapshot
# ---------------------------------------------------------------------------

print("\n=== Step 6: snapshot ===")
tl = resp.get("terminal_labels", [])
if tl:
    entry = tl[0]
    print(f"  terminal_label  : {entry.get('label')}")
    print(f"  blockers        : {entry.get('blockers')}")
if fc:
    print(f"  final_card[0]   : {json.dumps(fc[0])[:200]}")
print(f"  lowest_ceiling  : {ia.get('lowest_ceiling')}")
print(f"  skill_verif     : {ia.get('skill_verification_status')}")
print(f"  invoked_skills  : {len(ia.get('invoked_skills', []))} skills")
print(f"  unique_theses_count   : {ia.get('unique_theses_count')}")
print(f"  duplicate_groups_count: {ia.get('duplicate_groups_count')}")

print("\n✅  ALL CHECKS PASSED — PLAY path confirmed.")
sys.exit(0)
