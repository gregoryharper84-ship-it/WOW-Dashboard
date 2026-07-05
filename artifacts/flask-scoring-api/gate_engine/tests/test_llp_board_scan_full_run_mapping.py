"""
Tests for LLP PATCH — BOARD SCAN TO FULL RUN ESCALATION.

app.py is a monolithic Flask entrypoint that is unsafe to `import` directly
in a test process (it starts background cron threads / DB connections at
module scope). To test the *actual* production mapping functions (not a
reimplementation of them), this file extracts their exact source text out
of app.py by line range and execs it into an isolated namespace that only
provides the real dependencies those functions use (`datetime`, `timezone`,
`LLPLabel`, `run_llp_governance`). This keeps the test bound to the real
code — any edit to the functions in app.py is picked up automatically.
"""
import ast
import os
from datetime import datetime, timezone

import pytest

from gate_engine.llp_governance import LLPLabel, run_llp_governance, BANNED_AS_FINAL, validate_llp_label

APP_PY = os.path.join(os.path.dirname(__file__), "../../app.py")


def _load_functions(*names):
    src = open(APP_PY).read()
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    ns = {
        "datetime": datetime,
        "timezone": timezone,
        "LLPLabel": LLPLabel,
        "run_llp_governance": run_llp_governance,
    }
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in names:
            snippet = "".join(lines[node.lineno - 1:node.end_lineno])
            exec(compile(snippet, f"<app.py:{node.name}>", "exec"), ns)
            found.add(node.name)
    missing = set(names) - found
    if missing:
        raise AssertionError(f"Could not locate function(s) in app.py: {missing}")
    return ns


_ns = _load_functions(
    "_llp_requested_label_from_analysis",
    "_llp_governance_candidate_from_analysis",
)
_llp_requested_label_from_analysis = _ns["_llp_requested_label_from_analysis"]
_llp_governance_candidate_from_analysis = _ns["_llp_governance_candidate_from_analysis"]


def _load_board_scan_to_full_run(stub_board_scan, stub_analyze_one, stub_log_postmortem):
    """Extract the real `_llp_board_scan_to_full_run` orchestrator out of app.py
    (same technique as `_load_functions` above) so this test exercises the
    actual production control flow — not a reimplementation of it — while
    stubbing only its three external side-effecting dependencies (board scan
    data source, per-candidate full analysis, and postmortem logging)."""
    src = open(APP_PY).read()
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    ns = {
        "datetime": datetime,
        "timezone": timezone,
        "LLPLabel": LLPLabel,
        "run_llp_governance": run_llp_governance,
        "_llp_board_scan": stub_board_scan,
        "_llp_analyze_one": stub_analyze_one,
        "_llp_log_postmortem": stub_log_postmortem,
        "_llp_requested_label_from_analysis": _llp_requested_label_from_analysis,
        "_llp_governance_candidate_from_analysis": _llp_governance_candidate_from_analysis,
    }
    const_names = {
        "_LLP_BOARD_SCAN_TOP_N_MAX", "_LLP_BOARD_SCAN_TOP_N_DEFAULT",
        "_LLP_BOARD_SCAN_INCOMPLETE_MESSAGE", "_LLP_BOARD_SCAN_FALLBACK_TAGS",
    }
    found_consts = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id in const_names:
            snippet = "".join(lines[node.lineno - 1:node.end_lineno])
            exec(compile(snippet, f"<app.py:{node.targets[0].id}>", "exec"), ns)
            found_consts.add(node.targets[0].id)
    missing_consts = const_names - found_consts
    if missing_consts:
        raise AssertionError(f"Could not locate constant(s) in app.py: {missing_consts}")

    found = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_llp_board_scan_to_full_run":
            snippet = "".join(lines[node.lineno - 1:node.end_lineno])
            exec(compile(snippet, "<app.py:_llp_board_scan_to_full_run>", "exec"), ns)
            found = ns["_llp_board_scan_to_full_run"]
            break
    if found is None:
        raise AssertionError("Could not locate _llp_board_scan_to_full_run in app.py")
    return found


def _rec(**kwargs):
    base = {
        "sport": "NBA", "away_team": "Away Team", "home_team": "Home Team",
        "side": "Home Team", "market": "h2h",
        "book": "DraftKings", "current_line": -150, "opening_line": -140,
        "no_vig_implied_probability": 0.58, "model_win_probability": 0.64,
        "edge": 0.06, "kelly_stake": 0.75,
        "llp_badge": "ANCHOR", "final_decision": "BET",
        "discovery_clean": True, "validation_clean": True,
        "discovery": {}, "failure_paths": [],
    }
    base.update(kwargs)
    return base


def _scan_row(**kwargs):
    base = {
        "sport": "NBA", "home_team": "Home Team", "away_team": "Away Team",
        "side": "Home Team", "opponent": "Away Team",
        "book": "DraftKings", "american_odds": -150,
        "no_vig_implied_probability": 0.58,
        "commence_time": "2026-07-04T23:00:00Z",
    }
    base.update(kwargs)
    return base


class TestRequestedLabelMapping:
    def test_incomplete_record_falls_back_to_scout(self):
        assert _llp_requested_label_from_analysis(_rec(current_line=None)) == LLPLabel.SCOUT.value
        assert _llp_requested_label_from_analysis(_rec(edge=None)) == LLPLabel.SCOUT.value
        assert _llp_requested_label_from_analysis(None) == LLPLabel.SCOUT.value

    def test_anchor_bet_maps_to_approved(self):
        rec = _rec(llp_badge="ANCHOR", final_decision="BET")
        assert _llp_requested_label_from_analysis(rec) == LLPLabel.APPROVED.value

    def test_bet_or_qualified_maps_to_playable(self):
        rec = _rec(llp_badge="BET", final_decision="BET")
        assert _llp_requested_label_from_analysis(rec) == LLPLabel.PLAYABLE.value
        rec2 = _rec(llp_badge="QUALIFIED", final_decision="SMALL BET")
        assert _llp_requested_label_from_analysis(rec2) == LLPLabel.PLAYABLE.value

    def test_everything_else_maps_to_reject(self):
        rec = _rec(llp_badge="PASS", final_decision="PASS")
        assert _llp_requested_label_from_analysis(rec) == LLPLabel.REJECT.value
        rec2 = _rec(llp_badge="WAIT", final_decision="WATCH")
        assert _llp_requested_label_from_analysis(rec2) == LLPLabel.REJECT.value


class TestGovernanceCandidateMapping:
    def test_candidate_carries_required_price_edge_fields(self):
        rec = _rec()
        row = _scan_row()
        candidate = _llp_governance_candidate_from_analysis(
            rec, row, LLPLabel.APPROVED.value, "2026-07-04"
        )
        for field in ("book", "odds", "line", "side", "market", "timestamp",
                      "model_probability", "no_vig_probability", "edge", "source"):
            assert candidate.get(field) not in (None, ""), f"missing {field}"
        assert candidate["odds"] == row["american_odds"]
        assert candidate["line"] == row["american_odds"]
        assert candidate["model_probability"] == rec["model_win_probability"]
        assert candidate["no_vig_probability"] == rec["no_vig_implied_probability"]
        assert candidate["game_start_time"] == row["commence_time"]
        assert candidate["final_lock_confirmed"] is False
        assert candidate["full_rerun_completed"] is True

    def test_calibration_ledger_has_all_required_fields(self):
        from gate_engine.llp_governance import CALIBRATION_LEDGER_FIELDS
        rec = _rec()
        row = _scan_row()
        candidate = _llp_governance_candidate_from_analysis(
            rec, row, LLPLabel.APPROVED.value, "2026-07-04"
        )
        ledger = candidate["calibration_ledger"]
        for field in CALIBRATION_LEDGER_FIELDS:
            assert field in ledger, f"calibration_ledger missing {field}"

    def test_wnba_sport_routes_to_correct_market_type(self):
        rec = _rec(sport="WNBA")
        row = _scan_row(sport="WNBA")
        candidate = _llp_governance_candidate_from_analysis(
            rec, row, LLPLabel.APPROVED.value, "2026-07-04"
        )
        assert "wnba" in candidate["market"].lower()

    def test_stale_line_and_unavailable_price_flags(self):
        rec = _rec(discovery={"stale_line": True}, current_line=None, book=None)
        row = _scan_row()
        candidate = _llp_governance_candidate_from_analysis(
            rec, row, LLPLabel.SCOUT.value, "2026-07-04"
        )
        assert candidate["stale_price"] is True
        assert candidate["unavailable_price"] is True


class TestIntegrationWithRealGovernance:
    """The mapping shim feeds real `run_llp_governance` — verify governance
    can only cap the requested label DOWN, never promote it, and that a
    weak/incomplete candidate never comes back APPROVED or PLAYABLE.
    """

    def test_high_quality_candidate_can_reach_approved(self):
        rec = _rec(llp_badge="ANCHOR", final_decision="BET",
                    model_win_probability=0.61, no_vig_implied_probability=0.52,
                    edge=0.09)
        row = _scan_row(commence_time=None)
        requested = _llp_requested_label_from_analysis(rec)
        assert requested == LLPLabel.APPROVED.value
        candidate = _llp_governance_candidate_from_analysis(rec, row, requested, "2026-07-04")
        result = run_llp_governance(candidate, session={})
        assert result["effective_label"] in (LLPLabel.APPROVED.value, LLPLabel.PLAYABLE.value,
                                              LLPLabel.WATCH.value)

    def test_thin_edge_favorite_is_capped_down_never_up(self):
        # Heavy favorite: high implied probability but edge below threshold —
        # governance must cap the requested label down, never approve it.
        rec = _rec(llp_badge="PASS", final_decision="PASS",
                    model_win_probability=0.99, no_vig_implied_probability=0.9728,
                    edge=0.0172, current_line=-20000)
        row = _scan_row(american_odds=-20000, no_vig_implied_probability=0.9728)
        requested = _llp_requested_label_from_analysis(rec)
        assert requested == LLPLabel.REJECT.value
        candidate = _llp_governance_candidate_from_analysis(rec, row, requested, "2026-07-04")
        result = run_llp_governance(candidate, session={})
        assert result["effective_label"] == LLPLabel.REJECT.value

    def test_missing_edge_never_produces_approved_or_playable(self):
        rec = _rec(edge=None)
        assert _llp_requested_label_from_analysis(rec) == LLPLabel.SCOUT.value

    def test_hard_kill_forces_reject_or_cut(self):
        rec = _rec(llp_badge="ANCHOR", final_decision="BET",
                    discovery={"stale_line": True}, current_line=None, book=None)
        row = _scan_row()
        requested = _llp_requested_label_from_analysis(rec)
        candidate = _llp_governance_candidate_from_analysis(rec, row, requested, "2026-07-04")
        result = run_llp_governance(candidate, session={})
        assert result["effective_label"] not in (LLPLabel.APPROVED.value, LLPLabel.PLAYABLE.value)


def _ranked_row(sport="NBA", side="Home Team", home="Home Team", away="Away Team", **kwargs):
    base = {
        "sport": sport, "home_team": home, "away_team": away,
        "side": side, "opponent": away if side == home else home,
        "book": "DraftKings", "american_odds": -150,
        "no_vig_implied_probability": 0.58,
        "commence_time": "2026-07-04T23:00:00Z",
    }
    base.update(kwargs)
    return base


class TestBoardScanOnlyCappedAtScout:
    """WOW-PATCH-2026-07-04-LLP-GPT-RECONCILE regression coverage: a board-scan
    candidate that is NOT auto-promoted into the full 14-step LLP workflow must
    never surface a terminal label above LLP_SCOUT — no matter how strong its
    board-scan-only market signal looks. Promotion into `full_run` is the only
    path to LLP_WATCH/PLAYABLE/APPROVED/REJECT/CUT.
    """

    def _run(self, ranked, top_n=1):
        fn = _load_board_scan_to_full_run(
            stub_board_scan=lambda sports, board_date: (ranked, "ok"),
            stub_analyze_one=lambda game, sport, board_date: _rec(
                sport=game["sport"], home_team=game["home"], away_team=game["away"],
                side=game["side"], llp_badge="ANCHOR", final_decision="BET",
                model_win_probability=0.61, no_vig_implied_probability=0.52, edge=0.09,
            ),
            stub_log_postmortem=lambda candidates, session, board_date: {"logged": len(candidates)},
        )
        return fn(sports=["nba"], board_date="2026-07-04", top_n=top_n)

    def test_unpromoted_row_capped_at_scout(self):
        ranked = [
            _ranked_row(home="Team A", away="Team B", side="Team A", no_vig_implied_probability=0.62),
            _ranked_row(home="Team C", away="Team D", side="Team C", no_vig_implied_probability=0.60),
        ]
        result = self._run(ranked, top_n=1)
        scan_rows = result["board_scan"]["ranked"]
        promoted_row, unpromoted_row = scan_rows[0], scan_rows[1]
        assert unpromoted_row["label"] == LLPLabel.SCOUT.value
        assert "label" not in promoted_row or promoted_row.get("label") != LLPLabel.SCOUT.value
        assert result["full_run"]["promoted_count"] == 1

    def test_promoted_row_can_exceed_scout_via_full_run(self):
        ranked = [_ranked_row(home="Team A", away="Team B", side="Team A")]
        result = self._run(ranked, top_n=1)
        promoted_result = result["full_run"]["results"][0]
        assert promoted_result["label"] in (LLPLabel.APPROVED.value, LLPLabel.PLAYABLE.value,
                                             LLPLabel.WATCH.value)

    def test_no_scout_only_row_ever_carries_a_higher_final_label(self):
        ranked = [
            _ranked_row(home=f"Team {i}", away=f"Opp {i}", side=f"Team {i}",
                        no_vig_implied_probability=0.55 + i * 0.01)
            for i in range(5)
        ]
        result = self._run(ranked, top_n=2)
        promoted_names = {r["home_team"] for r in ranked[:2]}
        for row in result["board_scan"]["ranked"]:
            if row["home_team"] not in promoted_names:
                assert row["label"] == LLPLabel.SCOUT.value


class TestBannedAndConditionalNeverInFinalOutput:
    """Guards WOW-PATCH-2026-07-04-CONDITIONAL-CLEANUP's LLP-lane requirement:
    'Conditional' and every other BANNED_AS_FINAL term must never appear as a
    final_label / requested_label / label value anywhere in the board-scan
    or full-run output, regardless of upstream badge/decision text.
    """

    def test_conditional_badge_input_never_survives_as_output_label(self):
        rec = _rec(llp_badge="CONDITIONAL", final_decision="CONDITIONAL")
        requested = _llp_requested_label_from_analysis(rec)
        assert requested not in BANNED_AS_FINAL
        assert requested == LLPLabel.REJECT.value

    def test_all_banned_terms_rejected_by_label_validator(self):
        for banned in BANNED_AS_FINAL:
            r = validate_llp_label(banned)
            assert r["passed"] is False, f"{banned} must never validate as a final label"

    def test_full_board_scan_output_contains_no_banned_terms(self):
        fn = _load_board_scan_to_full_run(
            stub_board_scan=lambda sports, board_date: (
                [_ranked_row(home="Team A", away="Team B", side="Team A"),
                 _ranked_row(home="Team C", away="Team D", side="Team C")], "ok"),
            stub_analyze_one=lambda game, sport, board_date: _rec(
                sport=game["sport"], home_team=game["home"], away_team=game["away"],
                side=game["side"], llp_badge="CONDITIONAL", final_decision="CONDITIONAL",
            ),
            stub_log_postmortem=lambda candidates, session, board_date: {"logged": len(candidates)},
        )
        result = fn(sports=["nba"], board_date="2026-07-04", top_n=1)
        all_labels = [row.get("label") for row in result["board_scan"]["ranked"]]
        all_labels += [row.get("label") for row in result["full_run"]["results"]]
        all_labels += [row.get("requested_label") for row in result["full_run"]["results"]]
        for label in all_labels:
            if label is not None:
                assert label not in BANNED_AS_FINAL
                assert "CONDITIONAL" not in label.upper()


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
