#!/usr/bin/env python3
"""Debug why the row isn't reaching FINAL_APPROVED."""
import json, os, sys, uuid, urllib.request, urllib.error
from datetime import date

BASE_URL = "http://localhost:25643"
API_KEY  = os.environ.get("SCORING_API_KEY", "")
TODAY    = date.today().isoformat()

def get(path):
    with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=30) as r:
        return json.loads(r.read())

def post(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data,
        headers={"Content-Type":"application/json","X-API-Key":API_KEY}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.code

gov  = get("/wow/governance/status")
HASH = gov["governance_hash"]

PLAYER    = "Test Player"
PROP_TYPE = "Points"
LINE      = 20.5
ENR_KEY   = f"{PLAYER.lower()}:{PROP_TYPE.lower()}"
GAME_LOG  = [22.0, 25.0, 23.0, 24.0, 26.0, 21.0, 28.0, 22.0, 25.0, 24.0]
SB_LINE   = 20.5

enr = {
    "opponent": "TestTeam B", "game_date": TODAY,
    "book_or_platform": "FanDuel", "odds_or_payout": -110,
    "data_timestamp": f"{TODAY}T10:00:00Z",
    "status_timestamp": f"{TODAY}T10:00:00Z",
    "role_timestamp": f"{TODAY}T10:00:00Z",
    "l5_values": [24.0, 26.0, 21.0, 28.0, 22.0],
    "l10_values": GAME_LOG,
    "l10_median": 24.0, "l10_mean": 24.0, "l5_line_used": LINE,
    "market_no_vig_probability": 0.52,
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
        "raw_probability":        0.58,
        "calibrated_probability": 0.57,
        "lower_bound":            0.52,
        "upper_bound":            0.62,
        "model_timestamp":        f"{TODAY}T10:00:00Z",
        "source_snapshot_id":     "snap-test-001",
        "calibration_method":     "platt_scaling",
    },
    "payout_context": {"american_odds": -110, "implied_prob": 0.524},
    "failure_path_matrix": {
        "PRIMARY_KILL_PATH": {
            "scenario":          "Line moves against position before tip-off",
            "probability_band":  "10-20%",
            "model_adjustment":  "Reduce edge estimate by 0.05 on adverse movement > 1.0",
            "evidence":          "Line movement > 1.0 precedes miss 18% of time",
        },
        "SECONDARY_KILL_PATH": {
            "scenario":          "Player logs reduced minutes due to blowout",
            "probability_band":  "8-15%",
            "model_adjustment":  "Apply 10% probability haircut when blowout risk elevated",
            "evidence":          "Blowout minutes restriction observed in 12% of sample games",
        },
        "BLACK_SWAN_PATH": {
            "scenario":          "Player scratched from lineup at last minute due to injury",
            "probability_band":  "2-5%",
            "model_adjustment":  "Immediate void on DNP confirmation",
            "evidence":          "Late scratch rate across NBA sample 2-3% per game",
            "void_dnp_risk":     True,
        },
    },
    "directional_exposure_tags": [], "provisional_label": "FINAL_APPROVED",
    "validation_status": "PASS",
    "game_log": GAME_LOG,
    "season_log": [21.0,23.0,22.0,24.0,25.0,20.0,27.0,21.0,24.0,23.0,
                   22.0,25.0,21.0,26.0,23.0,24.0,22.0,25.0,20.0,27.0],
    "sportsbook_line": SB_LINE, "best_available": SB_LINE, "consensus_line": SB_LINE,
    "clv_entry_price": -110,
    "status_payload": {"status":"ACTIVE","source":"Test","dnp_risk":False,"minutes_restriction":False},
}

row = {"player": PLAYER, "sport": "NBA", "prop_type": PROP_TYPE,
       "line": LINE, "direction": "MORE", "slate_date": TODAY,
       "board_source": "PrizePicks", "game": "TeamA vs TeamB"}

body = {
    "rows": [row], "target_date": TODAY,
    "enrichment": {ENR_KEY: enr}, "record_entries": False,
    "response_mode": "full",
    "expected_governance_hash": HASH,
    "session_id": f"sess-dbg-{uuid.uuid4().hex[:8]}",
    "research_run_id": f"run-dbg-{uuid.uuid4().hex[:8]}",
    "as_of": f"{TODAY}T10:00:00Z",
}

resp, status = post("/gate-engine/run", body)
print(f"HTTP {status}")
print(f"terminal_disposition: {resp.get('terminal_disposition')}")
print(f"validation_status:    {resp.get('validation_status')}")
print(f"final_card len:       {len(resp.get('final_card') or [])}")
print()

for tl in (resp.get("terminal_labels") or []):
    print(f"ROW terminal_label: {tl.get('label')}")
    for b in (tl.get("blockers") or []):
        print(f"  BLOCKER: {b}")

pl = resp.get("prop_ledger") or []
if pl:
    row0 = pl[0]
    print(f"\nprop_ledger[0].terminal_label: {row0.get('terminal_label')}")
    gates = row0.get("gates", {})
    for gname, gval in sorted(gates.items()):
        if isinstance(gval, dict):
            passed = gval.get("passed")
            if passed is False:
                print(f"\n  FAILING gate[{gname}]:")
                for k,v in gval.items():
                    print(f"    {k}: {v}")
            else:
                status_val = gval.get("market_status") or gval.get("code") or gval.get("grade") or ""
                mq = gval.get("money_qualified")
                es = gval.get("edge_score")
                extra = ""
                if status_val: extra += f" status={status_val}"
                if mq is not None: extra += f" mq={mq}"
                if es is not None: extra += f" es={es}"
                print(f"  gate[{gname}]: passed={passed}{extra}")
