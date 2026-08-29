"""
WOW-PATCH-2026-08-17-PROB-LEDGER-HANDOFF — Step 9 regression tests A–L.

Endpoint-shaped fixtures for the hydration/acquisition → prob-ledger →
live scorer handoff for MLB pitcher (K, Outs) and WNBA player props.
"""
import sys, os
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gate_engine.pipeline import run_pipeline, ProbabilityPipelineCounter
from gate_engine import pipeline as _pl_mod
from gate_engine import prob_ledger_schema as _pls
from gate_engine.labels import PropLabel

TODAY = date(2026, 8, 17)
SLATE = "2026-08-17"


@pytest.fixture(autouse=True)
def _no_live_network(monkeypatch):
    """PRE_EXISTING_CI_BLOCKER root-cause fix (2026-08-28).

    WNBA rows in this file omit market_comparison/news_contradiction_check,
    so gate_engine.wnba.evidence_acquisition's fallback router makes a real
    outbound HTTP call (ESPN athlete-news search) to try to fill them --
    there is no test-mode gate in production code that prevents this.
    Whether that live call succeeds and what it returns depends on real-time
    network reachability and ESPN's live content, which made these fixture-
    driven regression tests silently non-deterministic across environments
    (observed: reliably green with no network reachable, reliably red on a
    CI runner with real internet access -- confirmed via a control run
    against clean main, see PR history). Forcing the HTTP boundary itself to
    a deterministic "unreachable" outcome makes every assertion here exercise
    only the fixture data, matching the file's own intent (fixed historical
    fixtures), and matches the existing mocking pattern already used for
    external adapters in gate_engine/tests/test_wnba_evidence_acquisition.py.
    """
    def _no_network(*args, **kwargs):
        raise ConnectionError("live network disabled for regression tests")
    monkeypatch.setattr("requests.get", _no_network)


def _now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _stage2(raw=0.58, cal=0.56, lb=0.50, ub=0.62, method="platt", snap="snap-001"):
    return {
        "raw_probability":        raw,
        "calibrated_probability": cal,
        "lower_bound":            lb,
        "upper_bound":            ub,
        "model_timestamp":        "2026-08-17T12:00:00+00:00",
        "source_snapshot_id":     snap,
        "calibration_method":     method,
    }


def _wnba_row(player="A'ja Wilson", line=8.5, direction="MORE"):
    return {
        "player": player, "sport": "WNBA", "prop_type": "Rebounds",
        "line": line, "direction": direction, "slate_date": SLATE,
        "board_source": "PrizePicks",
    }


def _wnba_enr(line=8.5, market=True, role=True, stage2=None, snapshot_line=None):
    enr = {
        "game_log":   [9.0, 11.0, 8.0, 10.0, 12.0, 7.0, 9.0, 10.0, 11.0, 8.0, 9.0, 10.0],
        "season_log": [9.0, 10.0, 9.5, 8.5, 10.5, 9.0, 9.5, 10.0, 9.0, 10.0],
        "model_probability_ledger": dict(stage2 if stage2 is not None else _stage2()),
        # WNBA evidence-acquisition packet completeness (event + market fields)
        "event_status": "SCHEDULED",
        "role_timestamp": _now_iso(),
        "market_comparison": {"sportsbook_line": line, "source": "test_fixture"},
        "status_payload": {
            "status": "ACTIVE", "source": "Rotowire",
            "projected_minutes": 32,
            "dnp_risk": False, "minutes_restriction": False,
            "status_timestamp": _now_iso(),
        },
    }
    if role:
        # Complete V1 evidence fixture: exact-stat history and contextual
        # box-score history are separate objects, with ten role-comparable
        # games and all required opportunity dimensions.
        enr["box_score_log"] = [
            {
                "game_date": f"2026-08-{day:02d}",
                "opponent": f"OPP{day}",
                "minutes": 32,
                "points": 20,
                "rebounds": 10,
                "assists": 4,
                "field_goal_attempts": 15,
                "usage_rate": 0.275,
                "starter_flag": True,
                "role": "STARTER",
                "source_timestamp": _now_iso(),
            }
            for day in range(1, 11)
        ]
        enr.update({
            "projected_minutes": 32,
            "projected_pace": 81.5,
            "opponent_defense": {"def_rating": 103.5},
            "rest_days": 2,
            "blowout_probability": 0.12,
            "game_script": {"expected_margin": 4.0},
            "role_status": {
                "status": "ACTIVE",
                "usage_role": "STARTER",
                "starter_flag": True,
                "projected_minutes": 32,
                "role_timestamp": _now_iso(),
            },
        })
    if market:
        enr["market_no_vig_prob"] = 0.55
        enr["sportsbook_line"] = snapshot_line if snapshot_line is not None else line
        enr["best_available"] = enr["sportsbook_line"]
        enr["market_snapshot_at"] = "2026-08-17T11:55:00+00:00"
    return enr


def _mlb_row(player="Paul Skenes", prop_type="Strikeouts", line=6.5, direction="MORE"):
    return {
        "player": player, "sport": "MLB", "prop_type": prop_type,
        "line": line, "direction": direction, "slate_date": SLATE,
        "board_source": "PrizePicks",
    }


def _mlb_enr(line=6.5, market=True, stage2=None):
    enr = {
        "game_log":   [7.0, 8.0, 6.0, 9.0, 7.0, 5.0, 8.0, 7.0, 6.0, 8.0],
        "season_log": [7.0, 7.0, 6.5, 7.5, 8.0, 6.0, 7.0, 7.5, 6.5, 7.0],
        "pitch_count": 98, "workload": 96.5, "fatigue": "normal",
        "opponent_k_pct": 24.5, "handedness": "R", "park_factor": 1.02,
        "weather": "clear", "starter_confirmed": True,
        "model_probability_ledger": dict(stage2 if stage2 is not None else _stage2()),
    }
    if market:
        enr["market_no_vig_prob"] = 0.54
        enr["sportsbook_line"] = line
        enr["best_available"] = line
        enr["market_snapshot_at"] = "2026-08-17T11:50:00+00:00"
    return enr


def _run(rows, enrichment):
    return run_pipeline(rows, target_date=TODAY, enrichment=enrichment,
                        skip_data_contract=True)


def _row_by_player(result, player):
    for r in result["prop_ledger"]:
        if (r.get("player") or "").lower() == player.lower():
            return r
    raise AssertionError(f"row for {player} not found")


def _mk_54_fixture():
    """15 MLB K ×2 + 6 WNBA REB ×2 + 6 MLB Outs ×2 = 54 directional rows."""
    rows, enrichment = [], {}
    for i in range(15):
        p = f"Pitcher K{i}"
        enrichment[f"{p.lower()}:strikeouts"] = _mlb_enr()
        for d, raw in (("MORE", 0.58), ("LESS", 0.42)):
            rows.append(_mlb_row(player=p, direction=d))
            # per-direction stage-2 via row-level? enrichment shared; keep shared
    for i in range(6):
        p = f"Wnba R{i}"
        enrichment[f"{p.lower()}:rebounds"] = _wnba_enr()
        for d in ("MORE", "LESS"):
            rows.append(_wnba_row(player=p, direction=d))
    for i in range(6):
        p = f"Pitcher O{i}"
        enrichment[f"{p.lower()}:pitching outs"] = _mlb_enr(line=16.5)
        for d in ("MORE", "LESS"):
            rows.append(_mlb_row(player=p, prop_type="Pitching Outs",
                                 line=16.5, direction=d))
    return rows, enrichment


# ---------------------------------------------------------------------------
# A — full WNBA fixture → complete ledger → rank_eligible
# ---------------------------------------------------------------------------

def test_A_wnba_full_fixture_rank_eligible():
    result = _run([_wnba_row()], {"a'ja wilson:rebounds": _wnba_enr()})
    row = _row_by_player(result, "A'ja Wilson")
    pl = row["gates"]["prob_ledger"]
    assert row["rank_eligible"] is True
    assert pl["model_probability_complete"] is True
    assert pl["market_lane_available"] is True
    comp_names = {c for c in (row.get("prob_ledger_input") or {}).get("missing_fields", [])}
    assert "l10_distribution" not in comp_names
    assert "role_usage" not in comp_names


# ---------------------------------------------------------------------------
# B — role evidence missing → typed missing_fields, never generic
# ---------------------------------------------------------------------------

def test_B_wnba_missing_role_typed_blocker():
    result = _run([_wnba_row()], {"a'ja wilson:rebounds": _wnba_enr(role=False)})
    row = _row_by_player(result, "A'ja Wilson")
    assert row["rank_eligible"] is False
    diag = row.get("pipeline_diagnostic")
    assert diag is not None
    assert "role_usage" in diag["missing_fields"]
    assert diag["contract_version"] == _pls.CONTRACT_VERSION
    assert diag["row_id"] == row["row_id"]
    # never a bare/vague PROB_LEDGER_INCOMPLETE blocker
    assert not any(str(b).strip() == "PROB_LEDGER_INCOMPLETE" for b in row["blockers"])
    # typed blocker string present
    assert any(str(b).startswith("PROB_LEDGER_SCHEMA:v1:") for b in row["blockers"])


# ---------------------------------------------------------------------------
# C / D — MLB K and MLB Outs full fixtures → rank_eligible
# ---------------------------------------------------------------------------

def test_C_mlb_k_full_fixture_rank_eligible():
    result = _run([_mlb_row()], {"paul skenes:strikeouts": _mlb_enr()})
    row = _row_by_player(result, "Paul Skenes")
    assert row["rank_eligible"] is True
    assert row["gates"]["prob_ledger"]["model_probability_complete"] is True
    # unconditional failure_path inputs were populated by the adapter
    pli = row.get("prob_ledger_input") or {}
    assert pli.get("contract_version") == "v1"


def test_D_mlb_outs_full_fixture_rank_eligible():
    result = _run(
        [_mlb_row(prop_type="Pitching Outs", line=16.5)],
        {"paul skenes:pitching outs": _mlb_enr(line=16.5)},
    )
    row = _row_by_player(result, "Paul Skenes")
    assert row["rank_eligible"] is True
    assert row["gates"]["prob_ledger"]["model_probability_complete"] is True


# ---------------------------------------------------------------------------
# E — market no-vig absent, complete sporting model
# ---------------------------------------------------------------------------

def test_E_market_absent_model_complete():
    result = _run([_wnba_row()], {"a'ja wilson:rebounds": _wnba_enr(market=False)})
    row = _row_by_player(result, "A'ja Wilson")
    pl = row["gates"]["prob_ledger"]
    assert pl["model_probability_complete"] is True
    assert pl["market_lane_available"] is False
    assert row["market_status"] == "STALE_MARKET"
    assert row["rank_eligible"] is True          # sporting entry stands
    # money/edge lane held via typed blocker
    assert any(str(b).startswith("MARKET_LANE:STALE_MARKET") for b in row["blockers"])
    # label never FINAL_APPROVED / MONEY_QUALIFIED without market lane
    assert row["terminal_label"] not in (
        PropLabel.FINAL_APPROVED.value, PropLabel.MONEY_QUALIFIED.value,
    )


# ---------------------------------------------------------------------------
# F — stale/drifted line → REHYDRATE_REQUIRED
# ---------------------------------------------------------------------------

def test_F_stale_line_rehydrate_required():
    result = _run(
        [_wnba_row(line=8.5)],
        {"a'ja wilson:rebounds": _wnba_enr(line=8.5, snapshot_line=9.5)},
    )
    row = _row_by_player(result, "A'ja Wilson")
    pl = row["gates"]["prob_ledger"]
    assert pl["market_lane_available"] is False
    assert row["market_status"] == "REHYDRATE_REQUIRED"
    assert any(str(b).startswith("MARKET_LANE:REHYDRATE_REQUIRED") for b in row["blockers"])
    # old-line probability never attached to new line: money lane held
    assert row["terminal_label"] not in (
        PropLabel.FINAL_APPROVED.value, PropLabel.MONEY_QUALIFIED.value,
    )


# ---------------------------------------------------------------------------
# G — adapter drops a populated component → run-level contract breach
# ---------------------------------------------------------------------------

def test_G_adapter_drop_raises_contract_breach(monkeypatch):
    orig = _pl_mod._wnba_pla.build_ledger_input

    class _Dropper:
        def __init__(self, inner):
            object.__setattr__(self, "_inner", inner)
        def __getattr__(self, name):
            return getattr(object.__getattribute__(self, "_inner"), name)
        def to_ledger_payload(self):
            p = object.__getattribute__(self, "_inner").to_ledger_payload()
            p["components"] = [
                c for c in p["components"] if c.get("name") != "l10_distribution"
            ]
            return p

    monkeypatch.setattr(
        _pl_mod._wnba_pla, "build_ledger_input",
        lambda row, enr: _Dropper(orig(row, enr)),
    )
    result = _run([_wnba_row()], {"a'ja wilson:rebounds": _wnba_enr()})
    breaches = [b for b in result["run_blockers"]
                if _pls.PROBABILITY_PIPELINE_CONTRACT_BREACH in str(b)]
    assert breaches, f"expected contract breach, got {result['run_blockers']}"
    assert any("adapter_dropped_field" in b and "l10_distribution" in b for b in breaches)


# ---------------------------------------------------------------------------
# H — counter invariant: rows reconcile exactly once
# ---------------------------------------------------------------------------

def test_H_counter_reconciles_exactly_once():
    rows, enrichment = _mk_54_fixture()
    result = _run(rows, enrichment)
    c = result["probability_pipeline_counter"]
    assert c["rows_discovered"] == 54
    assert c["rows_discovered"] >= c["rows_acquired"] >= c["rows_hydrated"] >= c["rows_model_ready"]
    n_typed = len(result["pipeline_diagnostic"])
    assert c["ledgers_complete"] + n_typed == c["rows_hydrated"]
    assert not [b for b in result["run_blockers"]
                if _pls.PROBABILITY_PIPELINE_CONTRACT_BREACH in str(b)]
    assert c["can_execute"] is False


def test_H2_counter_class_breach_detection():
    c = ProbabilityPipelineCounter()
    c.counts.update(rows_discovered=2, rows_acquired=3, rows_hydrated=1,
                    rows_model_ready=1, ledgers_complete=1)
    breaches = c.reconcile(typed_blocker_rows=1)
    assert any("stage_monotonicity" in b for b in breaches)
    assert any("ledger_reconciliation" in b for b in breaches)
    with pytest.raises(KeyError):
        c.increment("nonexistent_stage")


# ---------------------------------------------------------------------------
# I — MORE and LESS both scored for MLB K; probabilities sum to ~1
# ---------------------------------------------------------------------------

def test_I_mlb_k_more_and_less_directions():
    row_more = _mlb_row(direction="MORE")
    row_less = _mlb_row(direction="LESS")
    row_more["row_id"] = "skenes-k-more"
    row_less["row_id"] = "skenes-k-less"
    enrichment = {
        "skenes-k-more": _mlb_enr(stage2=_stage2(raw=0.58, cal=0.56, lb=0.50, ub=0.62)),
        "skenes-k-less": _mlb_enr(stage2=_stage2(raw=0.42, cal=0.44, lb=0.38, ub=0.50)),
    }
    result = _run([row_more, row_less], enrichment)
    ledger_rows = {r["row_id"]: r for r in result["prop_ledger"]
                   if (r.get("player") or "").lower() == "paul skenes"}
    assert set(ledger_rows) == {"skenes-k-more", "skenes-k-less"}
    for r in ledger_rows.values():
        pl = r["gates"]["prob_ledger"]
        assert pl["probability_schema"]["complete"] is True
        assert r["rank_eligible"] is True
    # Assert on the ACTUAL emitted ledger probabilities, not fixture constants:
    # the merged canonical ledger for each direction retains the real model
    # outputs, and the two directions sum to ~1 within calibration tolerance.
    emitted = {rid: enrichment[rid]["model_probability_ledger"] for rid in ledger_rows}
    for rid, mpl in emitted.items():
        assert 0.0 < mpl["raw_probability"] < 1.0
        assert 0.0 < mpl["calibrated_probability"] < 1.0
    raw_sum = (emitted["skenes-k-more"]["raw_probability"]
               + emitted["skenes-k-less"]["raw_probability"])
    cal_sum = (emitted["skenes-k-more"]["calibrated_probability"]
               + emitted["skenes-k-less"]["calibrated_probability"])
    assert abs(raw_sum - 1.0) < 1e-9
    assert abs(cal_sum - 1.0) < 0.05  # calibration tolerance


# ---------------------------------------------------------------------------
# J — full Flask route through the live run controller
# ---------------------------------------------------------------------------

def test_J_full_route_54_rows():
    import app as app_mod
    from gate_engine.governance import get_governance_status

    rows, enrichment = _mk_54_fixture()
    today = date.today().isoformat()
    for r in rows:
        r["slate_date"] = today
    # The route's Stage-2 preflight requires supplied ledgers to be complete
    # (components included) — the GPT-supplied ledger contract.
    for enr in enrichment.values():
        enr["model_probability_ledger"]["components"] = [
            {"name": "market_no_vig", "weight": 0.45,
             "value": enr.get("market_no_vig_prob", 0.55)},
            {"name": "l10_distribution", "weight": 0.30,
             "value": enr["game_log"][:10]},
            {"name": "role_usage", "weight": 0.15, "value": {"proxy": True}},
        ]

    client = app_mod.app.test_client()
    body = {
        "rows": rows,
        "enrichment": enrichment,
        "target_date": today,
        "record_entries": False,
        "expected_governance_hash": get_governance_status()["governance_hash"],
        "session_id": "test-session-261",
        "research_run_id": "test-run-261",
        "as_of": f"{today}T12:00:00+00:00",
    }
    # /gate-engine/run is behind @require_api_key, which checks X-API-Key
    # against SCORING_API_KEY — not GPT_ACTION_SECRET (a different credential
    # for a different auth surface). The wrong env var here always sent an
    # empty/mismatched header and the route correctly 401'd.
    os.environ.setdefault("SCORING_API_KEY", "test-scoring-key")
    headers = {"X-API-Key": os.environ.get("SCORING_API_KEY", "")}
    resp = client.post("/gate-engine/run", json=body, headers=headers)
    assert resp.status_code == 200, resp.get_data(as_text=True)[:800]
    data = resp.get_json()
    ledger = data.get("prop_ledger") or []
    assert len(ledger) == 54
    eligible = [r for r in ledger if r.get("rank_eligible")]
    assert len(eligible) > 0, "no rank_eligible rows on fully hydrated fixture"
    # every non-eligible hydrated row carries a typed diagnostic
    for r in ledger:
        if r.get("rank_eligible") or "pipeline_diagnostic" not in r:
            continue
        diag = r["pipeline_diagnostic"]
        assert diag["missing_fields"], diag
        assert "retryable" in diag


# ---------------------------------------------------------------------------
# K — outlier_recompute fires per-row on live scoring path
# ---------------------------------------------------------------------------

def test_K_outlier_recompute_fires_per_row(monkeypatch):
    calls = []
    orig_run = _pl_mod._or_mod.run

    def _spy(row, enrichment=None):
        calls.append(row.get("row_id"))
        return orig_run(row, enrichment=enrichment)

    monkeypatch.setattr(_pl_mod._or_mod, "run", _spy)

    # Force the outlier flag on the row via a blocker injected by prob_ledger
    # path: patch outlier flag through a first-pass blocker.
    orig_pl_run = _pl_mod.prob_ledger.run
    flagged = {"done": False}

    def _pl_spy(row, enrichment=None):
        res = orig_pl_run(row, enrichment=enrichment)
        if not flagged["done"]:
            row.setdefault("blockers", []).append(
                "OUTLIER_FLAG:REVIEW_REQUIRED:test_injected"
            )
            flagged["done"] = True
        return res

    monkeypatch.setattr(_pl_mod.prob_ledger, "run", _pl_spy)
    result = _run([_wnba_row()], {"a'ja wilson:rebounds": _wnba_enr()})
    row = _row_by_player(result, "A'ja Wilson")
    assert calls, "outlier_recompute did not fire per-row"
    assert row.get("outlier_recompute_status") is not None
    assert result["outlier_recompute_report"]["recomputed_count"] >= 1


# ---------------------------------------------------------------------------
# L — the 54-evaluation slate replay
# ---------------------------------------------------------------------------

def test_L_54_row_slate_replay():
    rows, enrichment = _mk_54_fixture()
    result = _run(rows, enrichment)
    ledger = result["prop_ledger"]
    assert len(ledger) == 54

    eligible = [r for r in ledger if r.get("rank_eligible")]
    assert len(eligible) > 0
    for r in ledger:
        pl = (r.get("gates") or {}).get("prob_ledger")
        if pl is None:
            continue  # row terminated before hydration
        if r.get("rank_eligible"):
            assert pl["model_probability_complete"] is True
        else:
            diag = r.get("pipeline_diagnostic")
            assert diag is not None, (r["row_id"], r.get("blockers"))
            assert diag["missing_fields"], diag
            assert isinstance(diag["retryable"], bool)
    # run summary carries the diagnostics whenever any row failed eligibility
    if len(eligible) < len([r for r in ledger if (r.get("gates") or {}).get("prob_ledger")]):
        assert result["pipeline_diagnostic"]


# ---------------------------------------------------------------------------
# M — live sequencing: specialist output completes the ledger BEFORE
#     classifier/FMCG run (not merely in the returned report)
# ---------------------------------------------------------------------------

def test_M_generative_output_completes_ledger_before_classify_and_fmcg(monkeypatch):
    """
    Row starts WITHOUT supplied Stage-2 fields; the REAL, unmodified WNBA
    generative model produces them mid-loop (raw/calibrated probabilities,
    stress lower bound, optimistic upper bound, model_timestamp, and
    calibration_method are all genuine model emissions).  The per-row
    finalize must fold them into the ledger and re-run prob_ledger BEFORE
    classifier.classify and FMCG fire, so live classification sees
    model_probability_complete=True.
    """
    seen_at_classify = {}
    seen_at_fmcg = {}
    orig_classify = _pl_mod.classifier.classify
    orig_gk = _pl_mod._fmcg.apply_gatekeeper

    def _classify_spy(row):
        seen_at_classify[row.get("row_id")] = row.get("model_probability_complete")
        return orig_classify(row)

    def _gk_spy(row):
        seen_at_fmcg[row.get("row_id")] = row.get("model_probability_complete")
        return orig_gk(row)

    monkeypatch.setattr(_pl_mod.classifier, "classify", _classify_spy)
    monkeypatch.setattr(_pl_mod._fmcg, "apply_gatekeeper", _gk_spy)

    enr = _wnba_enr()
    del enr["model_probability_ledger"]          # no supplied Stage-2 fields
    enr["source_snapshot_id"] = "snap-live-M"

    result = _run([_wnba_row()], {"a'ja wilson:rebounds": enr})
    row = _row_by_player(result, "A'ja Wilson")
    rid = row["row_id"]

    # The live path saw the COMPLETED ledger at classify/FMCG time
    assert seen_at_classify.get(rid) is True, seen_at_classify
    assert seen_at_fmcg.get(rid) is True, seen_at_fmcg
    # And the final report agrees
    assert row["rank_eligible"] is True
    assert row["gates"]["prob_ledger"]["model_probability_complete"] is True
    # FMCG never held the row for an incomplete model probability contract
    assert not any("MODEL_PROBABILITY_INCOMPLETE" in str(b) for b in row["blockers"])


# ---------------------------------------------------------------------------
# N / O — never-fabricated Stage-2 fields: absent upper_bound or
#         model_timestamp stays ineligible with a typed diagnostic
# ---------------------------------------------------------------------------

def _assert_ineligible_with_missing(result, player, field):
    row = _row_by_player(result, player)
    assert row["rank_eligible"] is False
    assert row["gates"]["prob_ledger"]["model_probability_complete"] is False
    diag = row.get("pipeline_diagnostic")
    assert diag is not None
    assert field in diag["missing_fields"], diag["missing_fields"]
    assert any(str(b).startswith("PROB_LEDGER_SCHEMA:v1:") for b in row["blockers"])
    return row


def test_N_absent_upper_bound_never_synthesized():
    # MLB lane: no model source for upper_bound exists, so a supplied ledger
    # lacking it must stay incomplete — the adapter never derives one.
    s2 = _stage2()
    del s2["upper_bound"]
    result = _run([_mlb_row()], {"paul skenes:strikeouts": _mlb_enr(stage2=s2)})
    row = _assert_ineligible_with_missing(result, "Paul Skenes", "upper_bound")
    # the adapter must not have fabricated a value into the canonical ledger
    assert "upper_bound" in (row.get("prob_ledger_input") or {}).get("missing_fields", [])


def test_O_absent_model_timestamp_never_synthesized():
    s2 = _stage2()
    del s2["model_timestamp"]
    enrichment = {"paul skenes:strikeouts": _mlb_enr(stage2=s2)}
    result = _run([_mlb_row()], enrichment)
    _assert_ineligible_with_missing(result, "Paul Skenes", "model_timestamp")
    # canonical ledger preserves the absence — no adapter-clock timestamp
    mpl = enrichment["paul skenes:strikeouts"]["model_probability_ledger"]
    assert mpl.get("model_timestamp") is None


def test_P_absent_calibration_method_never_inferred(monkeypatch):
    """Generative output WITHOUT calibration_method: the adapter must not
    infer a provenance string; the row stays ineligible with a typed
    missing field."""
    orig_gen = _pl_mod.wnba_generative_gate.run

    def _gen_stub(row, enr=None):
        orig_gen(row, enr=enr)
        row.setdefault("gates", {})["wnba_generative"] = {
            "raw_selected": 0.58, "cal_selected": 0.56,
            "cal_lower_bound": 0.50, "cal_upper_bound": 0.62,
            "model_timestamp": "2026-08-17T12:00:00+00:00",
            # calibration_method intentionally absent
            "model_status": "COMPLETE", "can_execute": False,
        }

    monkeypatch.setattr(_pl_mod.wnba_generative_gate, "run", _gen_stub)
    enr = _wnba_enr()
    del enr["model_probability_ledger"]
    enr["source_snapshot_id"] = "snap-live-P"
    result = _run([_wnba_row()], {"a'ja wilson:rebounds": enr})
    row = _assert_ineligible_with_missing(result, "A'ja Wilson", "calibration_method")
    mpl = None
    # the canonical ledger never carries a fabricated method string
    assert (row.get("prob_ledger_input") or {}).get("missing_fields") is not None
    assert "wnba_generative_role_regime_mixture" not in str(
        (row.get("gates") or {}).get("prob_ledger") or {}
    )


def test_Q_unsupported_contract_version_rejected():
    """A caller-supplied ledger payload carrying an unsupported
    contract_version is rejected at ingress with a typed blocker; its values
    do not reach the canonical ledger."""
    s2 = _stage2()
    enr = _wnba_enr(stage2=s2)
    enr["model_probability_ledger"]["contract_version"] = "v0"
    # make the supplied values distinctive so we can prove they were dropped
    enr["model_probability_ledger"]["raw_probability"] = 0.987654
    result = _run([_wnba_row()], {"a'ja wilson:rebounds": enr})
    row = _row_by_player(result, "A'ja Wilson")
    assert any("PROB_LEDGER_SCHEMA:UNSUPPORTED_CONTRACT_VERSION" in str(b)
               for b in row["blockers"]), row["blockers"]
    mpl = enr["model_probability_ledger"]
    # canonical payload replaced the rejected one; the distinctive supplied
    # value never became the scorer's probability
    assert mpl.get("raw_probability") != 0.987654
    assert mpl.get("contract_version") == "v1"
    # schema validation attached at ingress
    assert row.get("prob_ledger_schema_validation") is not None


# ---------------------------------------------------------------------------
# R / S / T — existing model output is handed to the canonical row ledger
# ---------------------------------------------------------------------------

def test_R_mlb_count_model_raw_result_is_recorded_without_calibration():
    """The existing centralized count model supplies a raw result only; this
    handoff must not create calibration, bounds, snapshot, or market facts."""
    row = _mlb_row()
    row["row_id"] = "handoff-k"
    enr = _mlb_enr()
    enr.pop("model_probability_ledger")

    _pl_mod._handoff_existing_model_probability(row, enr)

    assert row["candidate_evaluation_completed"] is True
    assert 0.0 < row["raw_model_probability"] < 1.0
    assert row["model_used"] == "poisson_l10"
    assert row["calibration_status"] == "PROVISIONAL"
    assert row["probability_publishable"] is False
    assert enr["model_probability_ledger"]["raw_probability"] == row["raw_model_probability"]
    for absent in ("calibrated_probability", "lower_bound", "upper_bound",
                   "model_timestamp", "source_snapshot_id", "calibration_method",
                   "market_no_vig"):
        assert absent not in enr["model_probability_ledger"]


def test_S_1ip_event_tree_raw_result_reaches_canonical_ledger():
    """A valid acquired 1IP packet reaches the canonical output once, without
    a second Poisson model or fabricated calibration."""
    row = {
        "row_id": "handoff-1ip",
        "sport": "MLB",
        "stat_key": "1IP_PITCHES_THROWN",
        "line": 15.5,
        "direction": "MORE",
    }
    enr = {
        "first_inning_bf_distribution": {
            "p_bf_3": 0.30, "p_bf_4": 0.40, "p_bf_gte5": 0.30,
        },
        "pitches_per_batter_distribution": {"mean": 4.2, "std": 1.1},
    }

    _pl_mod._handoff_existing_model_probability(row, enr)

    assert row["candidate_evaluation_completed"] is True
    assert 0.0 < row["raw_model_probability"] < 1.0
    assert row["model_used"] == "1ip_monte_carlo_event_tree_v1"
    assert row["gates"]["1ip_event_tree"]["selected_raw_probability"] == row["raw_model_probability"]
    assert enr["model_probability_ledger"]["raw_probability"] == row["raw_model_probability"]
    assert row["calibration_status"] == "PROVISIONAL"
    assert row["probability_publishable"] is False
    for absent in ("calibrated_probability", "lower_bound", "upper_bound",
                   "model_timestamp", "source_snapshot_id", "calibration_method"):
        assert absent not in enr["model_probability_ledger"]


def test_T_1ip_missing_pitches_per_batter_is_explicit_and_unpublished():
    row = {
        "row_id": "handoff-1ip-missing-ppb",
        "sport": "MLB",
        "stat_key": "1IP_PITCHES_THROWN",
        "line": 15.5,
        "direction": "MORE",
    }
    enr = {
        "first_inning_bf_distribution": {
            "p_bf_3": 0.30, "p_bf_4": 0.40, "p_bf_gte5": 0.30,
        },
    }

    _pl_mod._handoff_existing_model_probability(row, enr)

    assert row["candidate_evaluation_completed"] is False
    assert row["raw_model_probability"] is None
    assert row["probability_publishable"] is False
    assert row["model_probability_handoff"]["code"] == "1IP_EVENT_TREE_INPUT_INCOMPLETE"
    assert row["model_probability_handoff"]["missing_fields"] == ["pitches_per_batter_distribution"]


def test_U_route_preflight_model_components_do_not_require_market_component():
    """The HTTP preflight must mirror the model/market lane separation that
    the pipeline already enforces."""
    import app as app_mod
    from gate_engine.governance import get_governance_status

    today = date.today().isoformat()
    row = _mlb_row()
    row["slate_date"] = today
    enr = _mlb_enr()
    enr["model_probability_ledger"]["components"] = [
        {"name": "l10_distribution", "weight": 0.30, "value": enr["game_log"][:10]},
        {"name": "role_usage", "weight": 0.15, "value": {"starter_confirmed": True}},
    ]
    os.environ.setdefault("SCORING_API_KEY", "test-scoring-key")
    response = app_mod.app.test_client().post(
        "/gate-engine/run",
        json={
            "rows": [row],
            "enrichment": {"paul skenes:strikeouts": enr},
            "target_date": today,
            "record_entries": False,
            "expected_governance_hash": get_governance_status()["governance_hash"],
            "session_id": "test-session-model-only",
            "research_run_id": "test-run-model-only",
            "as_of": f"{today}T12:00:00+00:00",
        },
        headers={"X-API-Key": os.environ["SCORING_API_KEY"]},
    )

    assert response.status_code == 200, response.get_data(as_text=True)[:800]
    assert response.get_json().get("error_code") != "PROB_LEDGER_INCOMPLETE"


def test_V_supplied_stage2_raw_remains_the_single_canonical_value():
    """A complete caller ledger is authoritative; a local model must not
    generate a competing raw value or erase the supplied calibration facts."""
    row = _mlb_row()
    row["row_id"] = "supplied-stage2"
    enr = _mlb_enr(stage2=_stage2(raw=0.58, cal=0.56))

    _pl_mod._handoff_existing_model_probability(row, enr)

    assert row["candidate_evaluation_completed"] is True
    assert row["raw_model_probability"] == 0.58
    assert row["model_probability_ledger"]["raw_probability"] == 0.58
    assert row["model_probability_handoff"]["status"] == "SUPPLIED_MODEL_RESULT_PRESERVED"
    assert "calibration_status" not in row
    assert "probability_publishable" not in row


def test_W_invalid_market_weight_holds_only_market_lane():
    """A malformed market influence is actionable market data, not a reason
    to erase a complete sporting-model ledger."""
    from gate_engine import prob_ledger

    row = _wnba_row()
    payload = _stage2()
    payload["components"] = [
        {"name": "l10_distribution", "weight": 0.30, "value": [8, 9, 10]},
        {"name": "role_usage", "weight": 0.15, "value": {"starter": True}},
        {"name": "market_no_vig", "weight": 0.80, "value": 0.55},
    ]
    result = prob_ledger.run(row, enrichment={"model_probability_ledger": payload})

    assert result["model_probability_complete"] is True
    assert result["market_lane_available"] is False
    assert result["market_status"] == "REHYDRATE_REQUIRED"
    assert result["model_influence_violations"] == []
    assert result["market_influence_violations"]
