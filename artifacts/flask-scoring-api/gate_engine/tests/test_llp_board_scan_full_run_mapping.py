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


def _load_board_scan_to_full_run(stub_board_scan, stub_analyze_one, stub_log_postmortem,
                                 snapshot_cache=None):
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
        "_LLP_EVENT_SNAPSHOT_CACHE": snapshot_cache if snapshot_cache is not None else {},
        "app": type("A", (), {"logger": type("L", (), {
            "exception": staticmethod(lambda *a, **k: None),
            "warning":   staticmethod(lambda *a, **k: None),
        })()})(),
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


# ---------------------------------------------------------------------------
# WOW-PATCH-2026-08-17-MONEYLINE-MARKET-SNAPSHOT
# Event-ID propagation: `_llp_board_scan` must carry the Odds API event `id`
# on every ranked row so downstream dedup/joins never fall back to fuzzy
# team-name matching (root cause 1 of the moneyline zero-book handoff).
# ---------------------------------------------------------------------------

def _load_board_scan(stub_fetch_odds, stub_extract_market):
    src = open(APP_PY).read()
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    class _Log:
        def warning(self, *a, **k):
            pass

    ns = {
        "_LLP_SPORT_MAP": {"wnba": "basketball_wnba"},
        "_llp_fetch_odds": stub_fetch_odds,
        "_llp_extract_market": stub_extract_market,
        "_LLP_TEAM_ALIASES": {},
        "_llp_cache_event_snapshot": lambda snap: None,
        "app": type("A", (), {"logger": _Log()})(),
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_llp_board_scan":
            snippet = "".join(lines[node.lineno - 1:node.end_lineno])
            exec(compile(snippet, "<app.py:_llp_board_scan>", "exec"), ns)
            return ns["_llp_board_scan"]
    raise AssertionError("Could not locate _llp_board_scan in app.py")


def test_board_scan_propagates_odds_api_event_id():
    events = [{
        "id": "evt-odds-api-777",
        "home_team": "Minnesota Lynx",
        "away_team": "New York Liberty",
        "commence_time": "2026-08-17T23:00:00Z",
    }]

    def stub_fetch(sport_key, regions="us", markets=None):
        assert sport_key == "basketball_wnba"
        return events

    def stub_extract(event, market_key, side, line=None):
        return {"book": "draftkings", "american": -130, "novig_prob": 0.55}

    board_scan = _load_board_scan(stub_fetch, stub_extract)
    ranked, source_status = board_scan(["WNBA"], "2026-08-17")

    assert source_status == {"WNBA": "ok"}
    assert len(ranked) == 2  # both sides of the one event
    for row in ranked:
        assert row["event_id"] == "evt-odds-api-777"
        assert row["home_team"] == "Minnesota Lynx"
        assert row["away_team"] == "New York Liberty"


# ---------------------------------------------------------------------------
# Board scan → full run: cached snapshot / event identity must reach the
# promotion's actual scoring path, and a handoff breach must block scoring.
# ---------------------------------------------------------------------------

from gate_engine.moneyline.market_snapshot import (
    build_snapshot_from_odds_event as _build_ml_snap,
    MARKET_PIPELINE_CONTRACT_BREACH as _ML_BREACH,
)


def _raw_odds_event(n_books=3, corrupt=False):
    outcomes = [
        {"name": "Home Team", "price": -150},
        {"name": "Away Team", "price": +130},
    ]
    if corrupt:
        outcomes = [{"name": "???", "price": -150}, {"name": "???", "price": 130}]
    return {
        "id": "evt-fullrun-1",
        "home_team": "Home Team",
        "away_team": "Away Team",
        "commence_time": "2026-08-17T23:00:00Z",
        "bookmakers": [
            {"key": f"book{i}", "markets": [{
                "key": "h2h",
                "last_update": "2026-08-17T12:00:00Z",
                "outcomes": [dict(o) for o in outcomes],
            }]}
            for i in range(n_books)
        ],
    }


def _scan_ranked_row():
    return {
        "sport": "NBA", "event_id": "evt-fullrun-1",
        "home_team": "Home Team", "away_team": "Away Team",
        "side": "Home Team", "opponent": "Away Team",
        "book": "book0", "american_odds": -150,
        "no_vig_implied_probability": 0.58,
        "commence_time": "2026-08-17T23:00:00Z",
    }


def test_full_run_passes_cached_snapshot_and_event_id_to_analysis():
    snap = _build_ml_snap(_raw_odds_event(3), "NBA")
    cache = {"evt-fullrun-1": snap.to_dict()}
    seen_games = []

    def stub_scan(sports, board_date):
        return [_scan_ranked_row()], {"NBA": "ok"}

    def stub_analyze(game, default_sport, board_date):
        seen_games.append(game)
        return _rec()

    fn = _load_board_scan_to_full_run(stub_scan, stub_analyze, lambda *a, **k: None,
                                      snapshot_cache=cache)
    out = fn(["NBA"], "2026-08-17", 1)

    assert len(seen_games) == 1
    game = seen_games[0]
    # Event identity preserved into the actual scoring path
    assert game["event_id"] == "evt-fullrun-1"
    # Cached snapshot handed to analysis — no re-fetch/re-interpretation
    assert game["market_snapshot"]["event_id"] == "evt-fullrun-1"
    books = game["sportsbook_odds"]
    assert len(books) == 6  # 3 books × 2 sides, scorer-flat shape
    assert all({"team", "odds", "bookmaker"} <= set(b) for b in books)
    # Handoff observability surfaces on the full-run result record
    res = out["full_run"]["results"][0]
    assert res.get("market_pipeline", {}).get("counters", {}) \
              .get("books_sent_to_scorer") == 3


def test_full_run_blocks_scoring_on_snapshot_breach():
    snap = _build_ml_snap(_raw_odds_event(3, corrupt=True), "NBA")
    cache = {"evt-fullrun-1": snap.to_dict()}
    analyze_calls = []

    def stub_scan(sports, board_date):
        return [_scan_ranked_row()], {"NBA": "ok"}

    def stub_analyze(game, default_sport, board_date):
        analyze_calls.append(game)
        return _rec()

    fn = _load_board_scan_to_full_run(stub_scan, stub_analyze, lambda *a, **k: None,
                                      snapshot_cache=cache)
    out = fn(["NBA"], "2026-08-17", 1)

    # Scoring was BLOCKED — the analyzer never ran for the breached candidate
    assert analyze_calls == []
    res = out["full_run"]["results"][0]
    assert _ML_BREACH in res["failure_tags"]
    assert res["label"] == "LLP_SCOUT"
    assert res["stake_units"] == 0
    assert res["market_pipeline"]["status"] == _ML_BREACH
    assert res["market_pipeline"]["counters"]["books_fetched"] == 3
    assert res["market_pipeline"]["counters"]["books_sent_to_scorer"] == 0


# ---------------------------------------------------------------------------
# Real-resolver snapshot consumption: `_llp_resolve_market_from_snapshot`
# extracted from app.py and run against the REAL gate_engine odds resolver.
# A supplied valid snapshot must be consumed with NO live fetch; a breached
# snapshot must block analysis on the real path.
# ---------------------------------------------------------------------------

from gate_engine.llp_odds_resolver import resolve_odds_source as _real_resolver


def _load_snapshot_resolver(fetch_sentinel):
    class _Log:
        def exception(self, *a, **k):
            raise AssertionError(f"unexpected exception path: {a}")

    src = open(APP_PY).read()
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    ns = {
        "_resolve_odds_source": _real_resolver,
        # Sentinel: the snapshot path must NEVER touch the live fetcher
        "_llp_fetch_odds": fetch_sentinel,
        "app": type("A", (), {"logger": _Log()})(),
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) \
                and node.name == "_llp_resolve_market_from_snapshot":
            snippet = "".join(lines[node.lineno - 1:node.end_lineno])
            exec(compile(snippet, "<app.py:_llp_resolve_market_from_snapshot>",
                         "exec"), ns)
            return ns["_llp_resolve_market_from_snapshot"]
    raise AssertionError("_llp_resolve_market_from_snapshot not found in app.py")


def _fresh_record():
    return {"notes": [], "failure_paths": [], "contract_status": None}


def test_snapshot_is_the_market_source_no_live_fetch():
    def _fetch_sentinel(*a, **k):
        raise AssertionError("live fetch invoked despite valid snapshot")

    fn = _load_snapshot_resolver(_fetch_sentinel)
    snap = _build_ml_snap(_raw_odds_event(3), "NBA")
    record = _fresh_record()
    game = {"away": "Away Team", "home": "Home Team", "market": "h2h",
            "side": "Home Team", "market_snapshot": snap.to_dict()}
    resolution, blocked = fn(game, record, "h2h", "Home Team",
                             "2026-08-17", "basketball_nba", "nba")
    assert blocked is False
    assert resolution is not None and resolution.usable
    # Real resolver classified it as a live sportsbook source
    assert resolution.odds_source_quality is not None
    assert resolution.sportsbook_no_vig_available is True
    sel = resolution.sel
    assert sel["american"] == -150
    assert sel["novig_prob"] is not None and 0.5 < sel["novig_prob"] < 0.65
    # Event reconstructed by the shared adapter keeps the identity + books
    assert resolution.event["id"] == "evt-fullrun-1"
    assert len(resolution.event["bookmakers"]) == 3
    # Handoff counters stamped on the real analysis record
    assert record["market_pipeline"]["counters"]["books_sent_to_scorer"] == 3
    assert record["market_pipeline"]["status"] == "OK"


def test_snapshot_breach_blocks_real_analysis_path():
    fn = _load_snapshot_resolver(lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("live fetch invoked on breach path")))
    snap = _build_ml_snap(_raw_odds_event(3, corrupt=True), "NBA")
    record = _fresh_record()
    game = {"away": "Away Team", "home": "Home Team", "market": "h2h",
            "side": "Home Team", "market_snapshot": snap.to_dict()}
    resolution, blocked = fn(game, record, "h2h", "Home Team",
                             "2026-08-17", "basketball_nba", "nba")
    assert blocked is True
    assert resolution is None
    assert record["failure_paths"] == [_ML_BREACH]
    assert record["market_pipeline"]["status"] == _ML_BREACH
    assert record["market_pipeline"]["counters"]["books_sent_to_scorer"] == 0


def test_no_snapshot_falls_back_to_live_resolver():
    fn = _load_snapshot_resolver(lambda *a, **k: None)
    record = _fresh_record()
    resolution, blocked = fn({"market": "h2h", "side": "Home Team"}, record,
                             "h2h", "Home Team", "2026-08-17",
                             "basketball_nba", "nba")
    assert resolution is None and blocked is False
    assert "market_pipeline" not in record


def test_analyze_one_skips_live_resolver_when_snapshot_resolves():
    """Structural guard: in _llp_analyze_one the live `_resolve_odds_source`
    call (with _llp_fetch_odds) only executes on the fallback branch."""
    src = open(APP_PY).read()
    i = src.index("_llp_resolve_market_from_snapshot(\n        game, record")
    tail = src[i:i + 1600]
    assert "if _snap_blocked:" in tail and "return record" in tail
    assert "_resolution = _snap_resolution" in tail
    # The live-fetch resolver call sits inside the else branch after this hook
    j = tail.index("_resolution = _snap_resolution")
    assert "else:" in tail[j:]
    assert "fetch_odds_fn=_llp_fetch_odds" in tail[j:]


def test_partial_snapshot_blocks_analysis_and_never_fetches():
    """A supplied snapshot whose quotes cover only ONE side (partial handoff)
    must fail closed — the analyzer may not silently re-fetch live odds."""
    def _fetch_sentinel(*a, **k):
        raise AssertionError("live fetch invoked despite supplied snapshot")

    fn = _load_snapshot_resolver(_fetch_sentinel)
    snap = _build_ml_snap(_raw_odds_event(3), "NBA")
    # Strip the requested side's quotes → one-sided market, no two-sided no-vig
    snap.books = [q for q in snap.books if q.team != "Home Team"]
    record = _fresh_record()
    game = {"away": "Away Team", "home": "Home Team", "market": "h2h",
            "side": "Home Team", "market_snapshot": snap.to_dict()}
    resolution, blocked = fn(game, record, "h2h", "Home Team",
                             "2026-08-17", "basketball_nba", "nba")
    assert blocked is True and resolution is None
    assert record["failure_paths"] == ["MARKET_PIPELINE_CONTRACT_BREACH"]
    assert record["market_pipeline"]["status"] == "MARKET_PIPELINE_CONTRACT_BREACH"


def test_unresolvable_side_blocks_analysis_and_never_fetches():
    def _fetch_sentinel(*a, **k):
        raise AssertionError("live fetch invoked despite supplied snapshot")

    fn = _load_snapshot_resolver(_fetch_sentinel)
    snap = _build_ml_snap(_raw_odds_event(3), "NBA")
    record = _fresh_record()
    game = {"away": "Away Team", "home": "Home Team", "market": "h2h",
            "side": "Unrelated Club", "market_snapshot": snap.to_dict()}
    resolution, blocked = fn(game, record, "h2h", "Unrelated Club",
                             "2026-08-17", "basketball_nba", "nba")
    assert blocked is True and resolution is None
    assert record["failure_paths"] == ["MARKET_PIPELINE_CONTRACT_BREACH"]
    assert any("unresolvable" in n for n in record["notes"])


def test_opponent_stripped_snapshot_blocks_even_when_requested_side_survives():
    """One-sided snapshot with the REQUESTED side retained must still fail
    closed — the analyzer may not score the surviving side's vigged price."""
    def _fetch_sentinel(*a, **k):
        raise AssertionError("live fetch invoked despite supplied snapshot")

    fn = _load_snapshot_resolver(_fetch_sentinel)
    snap = _build_ml_snap(_raw_odds_event(3), "NBA")
    # Strip the OPPONENT's quotes; requested side "Home Team" keeps all quotes
    snap.books = [q for q in snap.books if q.team == "Home Team"]
    record = _fresh_record()
    game = {"away": "Away Team", "home": "Home Team", "market": "h2h",
            "side": "Home Team", "market_snapshot": snap.to_dict()}
    resolution, blocked = fn(game, record, "h2h", "Home Team",
                             "2026-08-17", "basketball_nba", "nba")
    assert blocked is True and resolution is None
    assert record["failure_paths"] == ["MARKET_PIPELINE_CONTRACT_BREACH"]
    assert any("one-sided" in n for n in record["notes"])


def test_full_run_blocks_opponent_stripped_snapshot_no_analysis():
    """Board scan → full run: a cached snapshot missing the opponent's quotes
    must block the candidate at analysis time (real snapshot consumption via
    the analyzer helper), never score the surviving side."""
    snap = _build_ml_snap(_raw_odds_event(3), "NBA")
    snap.books = [q for q in snap.books if q.team == "Home Team"]
    cache = {"evt-fullrun-1": snap.to_dict()}

    resolver = _load_snapshot_resolver(
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("live fetch invoked")))

    def stub_scan(sports, board_date):
        return [_scan_ranked_row()], {"NBA": "ok"}

    def stub_analyze(game, default_sport, board_date):
        # Mimic the real analyzer's snapshot consumption contract
        record = {"notes": [], "failure_paths": [], "contract_status": None,
                  **_rec()}
        _res, _blocked = resolver(game, record, "h2h", game["side"],
                                  board_date, "basketball_nba", "nba")
        assert _blocked is True, "analyzer must block one-sided snapshot"
        return record

    fn = _load_board_scan_to_full_run(stub_scan, stub_analyze, lambda *a, **k: None,
                                      snapshot_cache=cache)
    out = fn(["NBA"], "2026-08-17", 1)
    res = out["full_run"]["results"][0]
    assert "MARKET_PIPELINE_CONTRACT_BREACH" in (res.get("failure_paths") or [])


def test_supplied_empty_snapshot_blocks_analyzer_and_never_fetches():
    """game['market_snapshot'] = {} — key presence, not truthiness, marks a
    supplied snapshot; an empty one must fail closed with no live fetch."""
    def _fetch_sentinel(*a, **k):
        raise AssertionError("live fetch invoked despite supplied snapshot")

    fn = _load_snapshot_resolver(_fetch_sentinel)
    record = _fresh_record()
    game = {"away": "Away Team", "home": "Home Team", "market": "h2h",
            "side": "Home Team", "market_snapshot": {}}
    resolution, blocked = fn(game, record, "h2h", "Home Team",
                             "2026-08-17", "basketball_nba", "nba")
    assert blocked is True and resolution is None
    assert record["failure_paths"] == ["MARKET_PIPELINE_CONTRACT_BREACH"]


def test_full_run_cached_empty_snapshot_reaches_analyzer_not_refetched():
    """Board-scan promotion: a cached empty snapshot dict must still be
    handed to the analyzer (presence semantics) so its supplied-snapshot
    validation blocks — never silently skipped."""
    cache = {"evt-fullrun-1": {}}
    seen_games = []

    def stub_scan(sports, board_date):
        return [_scan_ranked_row()], {"NBA": "ok"}

    def stub_analyze(game, default_sport, board_date):
        seen_games.append(game)
        return _rec()

    fn = _load_board_scan_to_full_run(stub_scan, stub_analyze, lambda *a, **k: None,
                                      snapshot_cache=cache)
    fn(["NBA"], "2026-08-17", 1)
    assert len(seen_games) == 1
    assert seen_games[0].get("market_snapshot") == {}


@pytest.mark.parametrize("cached_value", [None, "garbage", ["x"]])
def test_full_run_malformed_cached_snapshot_attached_not_refetched(cached_value):
    """Malformed cache values must still be handed to the analyzer as a
    supplied (sanitized-empty) snapshot — never silently omitted so the
    analyzer re-fetches live odds."""
    cache = {"evt-fullrun-1": cached_value}
    seen_games = []

    def stub_scan(sports, board_date):
        return [_scan_ranked_row()], {"NBA": "ok"}

    def stub_analyze(game, default_sport, board_date):
        seen_games.append(game)
        return _rec()

    fn = _load_board_scan_to_full_run(stub_scan, stub_analyze, lambda *a, **k: None,
                                      snapshot_cache=cache)
    fn(["NBA"], "2026-08-17", 1)
    assert len(seen_games) == 1
    assert seen_games[0].get("market_snapshot") == {}
