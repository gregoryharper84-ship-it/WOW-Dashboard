import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from gate_engine.board_intake import normalize_row
from gate_engine import auto_enrichment
from services import odds_api, status as status_service


def _row(player="LeBron James", sport="NBA", prop_type="Points",
         line=27.5, direction="MORE", row_id=None):
    raw = {
        "player": player, "sport": sport, "prop_type": prop_type,
        "line": line, "direction": direction, "slate_date": "2026-07-04",
    }
    if row_id:
        raw["row_id"] = row_id
    return normalize_row(raw)


def _patch_odds(monkeypatch, props):
    monkeypatch.setattr(
        odds_api, "fetch_all_props",
        lambda sport: (props, {"events": "AVAILABLE (remaining=99)", "props": "AVAILABLE (remaining=99)"}),
    )


def _patch_odds_failure(monkeypatch):
    monkeypatch.setattr(
        odds_api, "fetch_all_props",
        lambda sport: ([], {"events": "FAILED: quota exhausted", "props": "NOT_CALLED"}),
    )


def _patch_injuries(monkeypatch, injuries):
    monkeypatch.setattr(status_service, "get_injuries", lambda sport: (injuries, "AVAILABLE"))


def _patch_injuries_failure(monkeypatch):
    monkeypatch.setattr(status_service, "get_injuries", lambda sport: ({}, "FAILED: HTTP 503"))


def test_fills_market_line_when_missing(monkeypatch):
    _patch_odds(monkeypatch, [
        {"player": "LeBron James", "prop": "player_points", "side": "MORE",
         "line": 26.5, "bookmaker": "draftkings", "price": -110},
        {"player": "LeBron James", "prop": "player_points", "side": "MORE",
         "line": 27.0, "bookmaker": "fanduel", "price": -105},
    ])
    _patch_injuries(monkeypatch, {})

    row = _row()
    enrichment, status = auto_enrichment.build_auto_enrichment([row])

    key = "lebron james:points"
    assert enrichment[key]["sportsbook_line"] == 26.5
    assert enrichment[key]["best_available"] == 26.5  # MORE -> lowest line easiest to clear
    assert enrichment[key]["consensus_line"] == pytest.approx(26.75)
    assert status["sports"]["NBA"]["market"]["events"] == "AVAILABLE (remaining=99)"
    assert status["sports"]["NBA"]["market_props_found"] == 2


def test_best_available_for_under_side(monkeypatch):
    _patch_odds(monkeypatch, [
        {"player": "LeBron James", "prop": "player_points", "side": "LESS",
         "line": 26.5, "bookmaker": "draftkings", "price": -110},
        {"player": "LeBron James", "prop": "player_points", "side": "LESS",
         "line": 27.5, "bookmaker": "fanduel", "price": -105},
    ])
    _patch_injuries(monkeypatch, {})

    row = _row(direction="LESS")
    enrichment, _ = auto_enrichment.build_auto_enrichment([row])

    key = "lebron james:points"
    assert enrichment[key]["best_available"] == 27.5  # LESS -> highest line easiest to stay under


def test_never_overwrites_caller_supplied_market_line(monkeypatch):
    _patch_odds(monkeypatch, [
        {"player": "LeBron James", "prop": "player_points", "side": "MORE",
         "line": 99.0, "bookmaker": "draftkings", "price": -110},
    ])
    _patch_injuries(monkeypatch, {})

    row = _row()
    base = {"lebron james:points": {"sportsbook_line": 26.5}}
    enrichment, _ = auto_enrichment.build_auto_enrichment([row], base_enrichment=base)

    key = "lebron james:points"
    assert enrichment[key]["sportsbook_line"] == 26.5
    assert enrichment[key]["best_available"] == 99.0  # still fills the missing field


def test_unmapped_prop_type_leaves_market_fields_empty(monkeypatch):
    _patch_odds(monkeypatch, [
        {"player": "LeBron James", "prop": "player_points", "side": "MORE",
         "line": 26.5, "bookmaker": "draftkings", "price": -110},
    ])
    _patch_injuries(monkeypatch, {})

    row = _row(prop_type="Double-Double")  # not in the mapping table
    enrichment, _ = auto_enrichment.build_auto_enrichment([row])

    key = "lebron james:double-double"
    assert key not in enrichment or "sportsbook_line" not in enrichment.get(key, {})


def test_odds_api_failure_fills_nothing_and_reports_honestly(monkeypatch):
    _patch_odds_failure(monkeypatch)
    _patch_injuries(monkeypatch, {})

    row = _row()
    enrichment, status = auto_enrichment.build_auto_enrichment([row])

    key = "lebron james:points"
    assert "sportsbook_line" not in enrichment.get(key, {})
    assert status["sports"]["NBA"]["market"]["events"] == "FAILED: quota exhausted"
    assert status["sports"]["NBA"]["market_props_found"] == 0


def test_status_payload_auto_filled_from_espn(monkeypatch):
    _patch_odds(monkeypatch, [])
    _patch_injuries(monkeypatch, {
        "lebron james": {"flag": 1, "status_raw": "questionable"},
    })

    row = _row()
    enrichment, _ = auto_enrichment.build_auto_enrichment([row])

    key = "lebron james:points"
    payload = enrichment[key]["status_payload"]
    assert payload["status"] == "questionable"
    assert payload["source"] == "ESPN"
    assert payload["dnp_risk"] is False


def test_status_payload_never_overwrites_caller_supplied(monkeypatch):
    _patch_odds(monkeypatch, [])
    _patch_injuries(monkeypatch, {
        "lebron james": {"flag": 2, "status_raw": "out"},
    })

    row = _row()
    base = {"lebron james:points": {"status_payload": {"status": "ACTIVE", "source": "Rotowire"}}}
    enrichment, _ = auto_enrichment.build_auto_enrichment([row], base_enrichment=base)

    key = "lebron james:points"
    assert enrichment[key]["status_payload"]["source"] == "Rotowire"


def test_status_failure_leaves_status_payload_unset(monkeypatch):
    _patch_odds(monkeypatch, [])
    _patch_injuries_failure(monkeypatch)

    row = _row()
    enrichment, status = auto_enrichment.build_auto_enrichment([row])

    key = "lebron james:points"
    assert "status_payload" not in enrichment.get(key, {})
    assert status["sports"]["NBA"]["status"] == "FAILED: HTTP 503"


def test_injuries_by_sport_none_does_not_crash_and_reports_failure(monkeypatch):
    """
    Explicit regression test for the injuries_by_sport[sport] = None path:
    when ESPN status fetch fails, get_player_injury_flag() must NOT be
    called with a None cache (that would crash inside status.py, which
    only special-cases injuries_cache=None to mean "go fetch it yourself" —
    here it means "fetch already failed, don't retry, don't fabricate").
    Expected: no exception, no status_payload written, honest failure
    reported in auto_enrichment_status, row is otherwise unaffected.
    """
    _patch_odds(monkeypatch, [])
    _patch_injuries_failure(monkeypatch)  # get_injuries -> ({}, "FAILED: HTTP 503")

    row = _row(sport="WNBA")

    # Must not raise.
    enrichment, status = auto_enrichment.build_auto_enrichment([row])

    key = "lebron james:points"
    assert "status_payload" not in enrichment.get(key, {})
    assert status["sports"]["WNBA"]["status"] == "FAILED: HTTP 503"
    assert status["sports"]["WNBA"]["status_players_found"] == 0
    # Row itself is untouched by auto-enrichment failure — no blockers added,
    # no terminal_label set. run_pipeline's own gates (data_contract,
    # status_role, etc.) are solely responsible for any DATA_CONTRACT_FAIL /
    # HOLD outcome, not this module.
    assert row.get("terminal_label") is None
    assert row.get("blockers") == []


def test_unsupported_sport_is_skipped_cleanly(monkeypatch):
    row = _row(sport="PGA", prop_type="Points")
    enrichment, status = auto_enrichment.build_auto_enrichment([row])

    assert status["sports"]["PGA"]["market"] == "NOT_CALLED: sport not supported by odds_api"
    assert status["sports"]["PGA"]["status"] == "NOT_CALLED: sport not supported by status service"


def test_preserves_row_id_key_when_caller_used_it(monkeypatch):
    _patch_odds(monkeypatch, [
        {"player": "LeBron James", "prop": "player_points", "side": "MORE",
         "line": 26.5, "bookmaker": "draftkings", "price": -110},
    ])
    _patch_injuries(monkeypatch, {})

    row = _row(row_id="row_0_abc123")
    base = {"row_0_abc123": {}}
    enrichment, _ = auto_enrichment.build_auto_enrichment([row], base_enrichment=base)

    assert enrichment["row_0_abc123"]["sportsbook_line"] == 26.5
    assert "lebron james:points" not in enrichment


def test_duplicate_player_prop_rows_do_not_collide(monkeypatch):
    """
    Two rows for the SAME player + prop_type (e.g. a doubleheader, or two
    separate board entries) must each keep their own enrichment — not merge
    into one shared player:prop entry. Distinguished only by row_id.
    """
    _patch_odds(monkeypatch, [
        {"player": "LeBron James", "prop": "player_points", "side": "MORE",
         "line": 26.5, "bookmaker": "draftkings", "price": -110},
    ])
    _patch_injuries(monkeypatch, {
        "lebron james": {"flag": 1, "status_raw": "questionable"},
    })

    row_a = _row(row_id="row_0_aaa000")
    row_b = _row(row_id="row_1_bbb111")

    enrichment, _ = auto_enrichment.build_auto_enrichment([row_a, row_b])

    # First row in the batch resolves to the simple "player:prop" key
    # (back-compat with single-row-per-player-prop boards). The SECOND row
    # sharing that same player+prop must NOT merge into it — it gets its
    # own entry at its own row_id instead.
    assert enrichment["lebron james:points"]["sportsbook_line"] == 26.5
    assert enrichment["row_1_bbb111"]["sportsbook_line"] == 26.5
    assert "row_0_aaa000" not in enrichment
    assert enrichment["lebron james:points"] is not enrichment["row_1_bbb111"]

    # Simulate exactly how pipeline._get_enrichment() reads each row: check
    # row_id first, fall back to player:prop key. Both rows resolve to a
    # DISTINCT, correctly-populated entry — no cross-contamination.
    def _resolve(row):
        rid = row["row_id"]
        key = f"{row['player'].lower()}:{row['prop_type'].lower()}"
        return enrichment.get(rid) or enrichment.get(key) or {}

    resolved_a = _resolve(row_a)
    resolved_b = _resolve(row_b)
    assert resolved_a["sportsbook_line"] == 26.5
    assert resolved_b["sportsbook_line"] == 26.5
    assert resolved_a is not resolved_b


def test_row_key_end_to_end_attachment_through_pipeline(monkeypatch):
    """
    Production-shape end-to-end test proving the ACTUAL /gate-engine/run
    route flow — not just build_auto_enrichment() in isolation:

      Given raw_rows (as received from the HTTP request body, no row_id
        supplied by the caller — the common case)
      When the route normalizes rows once, carries the generated row_id
        back onto raw_rows (the fix for the row_id-desync bug), and
        build_auto_enrichment() attaches enrichment keyed by that row_id
      And run_pipeline() is then called with the SAME raw_rows (which now
        carry the row_id) and the resulting enrichment dict
      Then pipeline._get_enrichment() must retrieve the auto-fetched entry
        by the expected row_id (not silently miss it)
      And the auto-fetched market line must be visible inside the
        pipeline's own gate result (market_gate's "sportsbook_line"),
        proving real route-level attachment, not just a passing unit test.
    """
    from gate_engine import board_intake
    from gate_engine.pipeline import run_pipeline

    _patch_odds(monkeypatch, [
        {"player": "LeBron James", "prop": "player_points", "side": "MORE",
         "line": 26.5, "bookmaker": "draftkings", "price": -110},
    ])
    _patch_injuries(monkeypatch, {})

    # Step 1: raw_rows exactly as they arrive in the HTTP body — no row_id.
    # (Minimal canonical shape, matching the fixture used throughout
    # test_pipeline.py — data-contract completeness is out of scope here,
    # skip_data_contract=True below isolates this test to enrichment
    # attachment only.)
    # Use today's UTC date so slate_validation (which runs unconditionally,
    # outside the skip_data_contract guard) does not purge the row before
    # market_gate executes.  This test isolates enrichment attachment, not
    # slate date validation.
    from datetime import date as _date_cls
    _today = _date_cls.today().isoformat()

    raw_rows = [{
        "player": "LeBron James", "sport": "NBA", "prop_type": "Points",
        "line": 27.5, "direction": "MORE", "slate_date": _today,
        "board_source": "PrizePicks",
    }]

    # Step 2: replicate the exact app.py /gate-engine/run auto_enrich block.
    normalized_rows = board_intake.normalize_board(raw_rows)
    for raw_row, normalized_row in zip(raw_rows, normalized_rows):
        raw_row["row_id"] = normalized_row["row_id"]

    enrichment, _status = auto_enrichment.build_auto_enrichment(normalized_rows)

    generated_row_id = normalized_rows[0]["row_id"]
    assert generated_row_id.startswith("row_0_")
    # For a single, first-occurrence player:prop pair with no pre-existing
    # caller enrichment, build_auto_enrichment resolves to the simple
    # "player:prop" key (back-compat with every existing caller that never
    # supplies row_id). The critical property under test is NOT which key
    # it lands on — it's that pipeline._get_enrichment(), which checks
    # row_id first and falls back to player:prop, can actually retrieve it.
    # A row_id-desync bug (two different normalize_board() calls minting
    # two different uuids) would still pass this specific lookup by luck
    # via the key fallback — the desync is proven separately below by
    # comparing generated_row_id against the pipeline's own row_id.
    resolved = enrichment.get(generated_row_id) or enrichment.get("lebron james:points")
    assert resolved is not None, "enrichment not retrievable by row_id or player:prop key"
    assert resolved["sportsbook_line"] == 26.5

    # Step 3: call run_pipeline exactly as the route does — with the
    # now-row_id-carrying raw_rows and the auto-built enrichment.
    # Pass target_date so slate_validation accepts the row's slate_date.
    from datetime import date as _d
    result = run_pipeline(
        raw_rows,
        target_date=_d.today(),
        enrichment=enrichment,
        skip_data_contract=True,
    )

    # Step 4: pipeline normalized raw_rows AGAIN internally (pipeline.py's
    # own board_intake.normalize_board call) — prove it produced the SAME
    # row_id as the pre-pass, i.e. no desync.
    pipeline_row_id = result["prop_ledger"][0]["row_id"]
    assert pipeline_row_id == generated_row_id, (
        "row_id desync: pipeline's internal normalize_board() minted a "
        "different row_id than the auto-enrichment pre-pass — enrichment "
        "would silently fail to attach for callers who don't supply row_id."
    )

    # Step 5: the auto-fetched market line is visible inside the pipeline's
    # own gate result for this row — proves real attachment, not just that
    # build_auto_enrichment() returned a dict nobody consumed.
    market_result = result["prop_ledger"][0]["gates"].get("market_gate", {})
    assert market_result.get("sportsbook_line") == 26.5


def test_pitching_outs_market_key_routing(monkeypatch):
    """
    'pitching outs' / 'outs' prop types must route to the 'pitcher_outs' Odds API
    market key — not 'batter_outs'.

    Regression for WOW-PATCH-2026-08-06: _PROP_TYPE_TO_MARKET_SUFFIX had no entry
    for 'pitching outs'/'outs', so the market lookup silently returned None and the
    enrichment carried no sportsbook_line even when the Odds API had data.
    """
    from gate_engine.auto_enrichment import _market_key_for

    # Primary long form (from normalizer prop display)
    assert _market_key_for("MLB", "pitching outs") == "pitcher_outs", (
        "'pitching outs' must map to 'pitcher_outs', not 'batter_outs'"
    )
    # Short-form stat_key set by _norm_to_pipeline_row
    assert _market_key_for("MLB", "outs") == "pitcher_outs", (
        "'outs' stat_key must map to 'pitcher_outs'"
    )


def test_pitching_outs_is_in_pitcher_prop_types(monkeypatch):
    """
    'pitching outs' and 'outs' must be in _PITCHER_PROP_TYPES so the market
    prefix is 'pitcher' not 'batter'.
    """
    from gate_engine.auto_enrichment import _PITCHER_PROP_TYPES

    assert "pitching outs" in _PITCHER_PROP_TYPES, (
        "'pitching outs' must be in _PITCHER_PROP_TYPES"
    )
    assert "outs" in _PITCHER_PROP_TYPES, (
        "'outs' must be in _PITCHER_PROP_TYPES so stat_key short-form routes to pitcher prefix"
    )


def test_pitcher_outs_in_player_logs_prop_stat_map():
    """'pitcher_outs' must be registered in services/player_logs.py PROP_STAT_MAP."""
    from services.player_logs import PROP_STAT_MAP

    assert "pitcher_outs" in PROP_STAT_MAP, (
        "'pitcher_outs' must be in PROP_STAT_MAP — ESPN stat field is 'outs'"
    )
    stat_fields = PROP_STAT_MAP["pitcher_outs"]
    assert "outs" in stat_fields, (
        f"PROP_STAT_MAP['pitcher_outs'] = {stat_fields!r} — must include 'outs'"
    )


def test_pitcher_outs_in_odds_api_supported_markets():
    """'pitcher_outs' must appear in the supported-markets list in services/odds_api.py."""
    import inspect
    import services.odds_api as odds_api_mod

    source = inspect.getsource(odds_api_mod)
    assert "pitcher_outs" in source, (
        "'pitcher_outs' must be listed in odds_api.py supported markets "
        "so the market isn't silently skipped during quota-aware fetches"
    )


def test_multi_sport_batch_fetches_each_sport_once(monkeypatch):
    calls = []

    def fake_fetch(sport):
        calls.append(sport)
        return [], {"events": "AVAILABLE", "props": "AVAILABLE"}

    monkeypatch.setattr(odds_api, "fetch_all_props", fake_fetch)
    _patch_injuries(monkeypatch, {})

    rows = [
        _row(player="A", sport="NBA"),
        _row(player="B", sport="NBA"),
        _row(player="C", sport="MLB", prop_type="Hits"),
    ]
    auto_enrichment.build_auto_enrichment(rows)

    assert sorted(calls) == ["MLB", "NBA"]
