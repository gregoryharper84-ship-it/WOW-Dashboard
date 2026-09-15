"""RT-A regression suite: cross-sport winner discovery and TheRundown repair.

Two defect classes are pinned here.

1. A full-model moneyline scan must *discover* every supported sport before the
   model registry is consulted, so the board stops collapsing to MLB whenever
   MLB is the only healthy bridge. A sport with no fitted model keeps its rows
   with a typed MODEL_UNAVAILABLE; it never loses them.

2. TheRundown is market evidence, never probability. A catalog payload must not
   reach the odds parser, a 429 must stay readable, identical slate requests
   must collapse to one provider call, and none of it may touch a completed
   sporting probability.

Every assertion here is about *which typed status* a row terminates with. None
of them may be relaxed to make a build green: collapsing an invoked-model
failure into MODEL_UNAVAILABLE is the exact regression this file exists to catch.
"""
from __future__ import annotations

import io
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from fastapi import HTTPException

from v17 import cross_sport_winner_discovery as discovery
from v17 import market_evidence_native_live as live
from v17 import market_evidence_observability as observability
from v17 import market_evidence_sources as sources
from v17 import rundown_payload_contract as payload_contract
from v17 import rundown_rate_limit as rate_limit
from v17 import rundown_snapshot_cache as snapshot_cache
from v17.team_event_bridge_runtime import (
    TEAM_EVENT_BRIDGES,
    register_team_event_bridge,
    score_registered_team_event_request,
    team_event_bridge_health,
)
from v17.team_event_capability_manifest import TEAM_EVENT_INPUT_CONTRACTS

SLATE_DATE = "2026-09-15"
SLATE_TZ = "UTC"


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolated_registry():
    original = dict(TEAM_EVENT_BRIDGES)
    TEAM_EVENT_BRIDGES.clear()
    try:
        yield
    finally:
        TEAM_EVENT_BRIDGES.clear()
        TEAM_EVENT_BRIDGES.update(original)


@pytest.fixture(autouse=True)
def _rundown_env(monkeypatch):
    for name in list(dict(__import__("os").environ)):
        if name.startswith(("WOW_RUNDOWN", "RUNDOWN_", "THERUNDOWN_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RUNDOWN_API_KEY", "test-key-not-a-real-credential")
    monkeypatch.setattr(sources, "ENABLED", True)
    monkeypatch.setenv("WOW_RUNDOWN_SPORT_ID_BASEBALL_MLB", "3")
    monkeypatch.setenv("WOW_RUNDOWN_SPORT_ID_AMERICANFOOTBALL_NFL", "2")
    snapshot_cache.reset()
    observability.reset()
    yield
    snapshot_cache.reset()
    observability.reset()


class _Response:
    def __init__(self, payload, status=200):
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(payload, status=200, calls=None):
    def open_it(request, timeout=None):
        if calls is not None:
            calls.append(request.full_url.split("?")[0])
        return _Response(payload, status)

    return open_it


def _http_error(status, headers=None, body=b""):
    return HTTPError("https://therundown.test/x", status, "err", headers or {}, io.BytesIO(body))


def _past_iso(hours=3):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _slate_iso(hour=18):
    return f"{SLATE_DATE}T{hour:02d}:00:00Z"


def _discovered(sport, event_id, *, status="SCHEDULED", commence=None, home="Alpha", away="Beta"):
    return {
        "id": event_id,
        "home_team": home,
        "away_team": away,
        "commence_time": commence or _slate_iso(),
        "status": status,
    }


def _feed(rows_by_sport):
    """Serve a family's rows once, from its first configured target.

    A family can have many provider targets (twelve soccer competitions), so a
    naive feed that answers every target with the same rows would be testing the
    fixture rather than the sweep.
    """
    served: set[str] = set()

    def fetch(family, target=None):
        if family in served:
            return []
        served.add(family)
        return rows_by_sport.get(family, [])

    return fetch


def _inventory(rows_by_sport, *, sports=None, now=None):
    return discovery.discover_winner_slate(
        requested_slate_date=SLATE_DATE,
        requested_timezone=SLATE_TZ,
        fetch_sport_events=_feed(rows_by_sport),
        supported_sports=sports or ("MLB", "NFL", "NHL", "SOCCER"),
        now=now,
    )


def _valid_package(candidate_id="candidate-1"):
    return {
        "candidate_id": candidate_id,
        "calibrated_probability": 0.72,
        "calibrated_lower_bound": 0.66,
        "calibrated_upper_bound": 0.78,
        "immutable_model_timestamp": "2026-09-15T17:00:00+00:00",
        "calibration_method": "ISOTONIC",
        "calibration_version": "v1",
        "source_snapshot_id": "snapshot-1",
        "source_snapshot_timestamp": "2026-09-15T16:55:00+00:00",
        "outcome_space": "TWO_WAY_WINNER",
        "model_version": "fitted-v1",
        "event_status": "PREGAME",
        "probability_publishable": True,
        "rank_eligible": True,
        "blockers": [],
        "can_execute": False,
    }


def _bridge_request(sport, *, complete=True, league=None):
    required = TEAM_EVENT_INPUT_CONTRACTS[sport]
    evidence = {field: f"verified:{field}" for field in required}
    core = {
        "requester_host_identity": "WOW_BETTING_ENGINE",
        "candidate_family": "TEAM_EVENT",
        "sport": sport,
        "league": league or sport,
        "official_event_id": "event-1",
        "home_team": "Alpha",
        "away_team": "Beta",
        "settlement_basis": "FULL_GAME",
        "sport_specific_evidence": evidence,
    }
    if not complete:
        # An input is only missing when it is absent from both the request field
        # and the sport-specific evidence bundle.
        evidence.pop(required[-1], None)
        core[required[-1]] = None
    return SimpleNamespace(**core)


def _register(sport, scorer, *, validate=True):
    return register_team_event_bridge(
        sport,
        adapter_name=f"TEST_{sport}_ADAPTER",
        controlling_specialist=f"TEST_{sport}_SPECIALIST",
        scorer=scorer,
        required_inputs=TEAM_EVENT_INPUT_CONTRACTS[sport],
        standard_package_validation=validate,
    )


def _registry_resolver(event):
    return TEAM_EVENT_BRIDGES.get(event.sport)


def _scan(rows_by_sport, *, score_row, sports=None, now=None):
    inventory = _inventory(rows_by_sport, sports=sports, now=now)
    rows = discovery.route_discovered_slate(
        inventory, resolve_model=_registry_resolver, score_row=score_row
    )
    return inventory, rows, discovery.reconcile(inventory, rows)


def _by_sport(rows):
    return {row.identity["sport"]: row for row in rows}


# ---------------------------------------------------------------------------
# RT-A01..RT-A06, RT-A15..RT-A20 — discovery, routing and the failure taxonomy
# ---------------------------------------------------------------------------

def test_rt_a01_discovery_is_independent_of_the_model_registry():
    _register("MLB", lambda *a, **k: _valid_package("mlb"))
    inventory, rows, audit = _scan(
        {
            "MLB": [_discovered("MLB", "mlb-1")],
            "NFL": [_discovered("NFL", "nfl-1")],
            "NHL": [_discovered("NHL", "nhl-1")],
            "SOCCER": [_discovered("SOCCER", "soccer-1")],
        },
        score_row=lambda event, model: model.scorer(
            _bridge_request(event.sport), event_api=object(), canonical_hydration_required=False
        ),
    )

    assert audit["events_discovered"] == 4
    assert len(rows) == 4
    by_sport = _by_sport(rows)
    assert by_sport["MLB"].bucket == discovery.MODEL_COMPLETED
    for sport in ("NFL", "NHL", "SOCCER"):
        assert by_sport[sport].bucket == "MODEL_UNAVAILABLE"
        assert by_sport[sport].rank_eligible is False
    assert audit["row_reconciliation"] == "PASS"
    assert audit["sports_queried"] == 4


def test_rt_a02_a_registered_non_mlb_model_is_selected_and_invoked():
    invoked: list[str] = []

    def nfl_scorer(req, *, event_api, canonical_hydration_required=False):
        invoked.append(req.sport)
        return _valid_package("nfl")

    _register("NFL", nfl_scorer)
    _, rows, audit = _scan(
        {"NFL": [_discovered("NFL", "nfl-1")]},
        score_row=lambda event, model: score_registered_team_event_request(
            _bridge_request(event.sport), event_api=object()
        ),
        sports=("NFL",),
    )

    assert invoked == ["NFL"]
    assert rows[0].bucket == discovery.MODEL_COMPLETED
    assert rows[0].model_status != "MODEL_UNAVAILABLE"
    assert rows[0].probability_publishable is True
    assert audit["row_reconciliation"] == "PASS"


def test_rt_a03_a_missing_model_is_model_unavailable_and_never_ranked():
    _, rows, _ = _scan(
        {"NHL": [_discovered("NHL", "nhl-1")]},
        score_row=lambda event, model: pytest.fail("scorer must not run without a model"),
        sports=("NHL",),
    )
    row = rows[0].as_dict()
    assert row["model_status"] == "MODEL_UNAVAILABLE"
    assert row["rank_eligible"] is False
    assert row["can_execute"] is False
    assert row["market_probability_substitution_allowed"] is False
    assert row["detail"]["model_invoked"] is False


def test_rt_a04_an_invoked_scorer_that_throws_is_never_model_unavailable():
    def exploding(req, *, event_api, canonical_hydration_required=False):
        raise RuntimeError("scorer blew up")

    _register("NFL", exploding)
    with pytest.raises(HTTPException) as caught:
        score_registered_team_event_request(_bridge_request("NFL"), event_api=object())

    detail = caught.value.detail
    assert detail["code"] == "MODEL_SCORER_FAILED"
    assert detail["code"] != "MODEL_UNAVAILABLE"
    assert detail["model_invoked"] is True
    assert detail["can_execute"] is False


def test_rt_a05_missing_sport_specific_inputs_are_inputs_insufficient():
    _register("NHL", lambda *a, **k: _valid_package("nhl"))
    with pytest.raises(HTTPException) as caught:
        score_registered_team_event_request(
            _bridge_request("NHL", complete=False), event_api=object()
        )

    detail = caught.value.detail
    assert detail["code"] == "MODEL_INPUTS_INSUFFICIENT"
    assert detail["missing_fields"], "the unresolved input must be named"
    assert TEAM_EVENT_INPUT_CONTRACTS["NHL"][-1] in detail["missing_fields"]


def test_rt_a06_an_inconsistent_probability_package_is_output_invalid():
    broken = {**_valid_package("nfl"), "calibrated_lower_bound": 0.60, "calibrated_probability": 0.55}
    _register("NFL", lambda *a, **k: broken)
    with pytest.raises(HTTPException) as caught:
        score_registered_team_event_request(_bridge_request("NFL"), event_api=object())

    detail = caught.value.detail
    assert detail["code"] == "MODEL_OUTPUT_INVALID"
    assert detail["rank_eligible"] is False


def test_rt_a15_a_high_quality_sportsbook_consensus_never_substitutes_for_a_model():
    # The market is available and unambiguous; the fitted model is not. The row
    # must stay MODEL_UNAVAILABLE rather than adopt a no-vig market probability.
    market_consensus = {"home_no_vig_probability": 0.71, "away_no_vig_probability": 0.29}
    _, rows, _ = _scan(
        {"NFL": [{**_discovered("NFL", "nfl-1"), "market_consensus": market_consensus}]},
        score_row=lambda event, model: pytest.fail("no model exists to score this row"),
        sports=("NFL",),
    )
    row = rows[0].as_dict()
    assert row["model_status"] == "MODEL_UNAVAILABLE"
    assert row["probability_publishable"] is False
    assert "calibrated_probability" not in row["detail"]
    assert row["detail"].get("model_probability") is None


def test_rt_a16_no_generic_reasoning_probability_is_produced_without_a_model():
    _, rows, _ = _scan(
        {"TENNIS": [_discovered("TENNIS", "atp-1")]},
        score_row=lambda event, model: pytest.fail("no model exists to score this row"),
        sports=("TENNIS",),
    )
    row = rows[0].as_dict()
    assert row["model_status"] == "MODEL_UNAVAILABLE"
    assert row["generic_reasoning_substitution_allowed"] is False
    assert not any(
        key in row["detail"]
        for key in ("calibrated_probability", "calibrated_lower_bound", "raw_probability")
    )


def test_rt_a17_at_most_one_terminal_side_survives_per_event():
    _register("MLB", lambda *a, **k: _valid_package("mlb"))
    _, rows, _ = _scan(
        {"MLB": [_discovered("MLB", "mlb-1")]},
        score_row=lambda event, model: model.scorer(
            _bridge_request(event.sport), event_api=object(), canonical_hydration_required=False
        ),
        sports=("MLB",),
    )
    publishable = [row for row in rows if row.probability_publishable]
    assert len(publishable) <= 1
    assert len({row.identity["event_key"] for row in rows}) == len(rows)


def test_rt_a18_every_discovered_row_is_accounted_for_exactly_once():
    _register("MLB", lambda *a, **k: _valid_package("mlb"))
    inventory, rows, audit = _scan(
        {
            "MLB": [_discovered("MLB", "mlb-1"), _discovered("MLB", "mlb-2", status="FINAL")],
            "NFL": [_discovered("NFL", "nfl-1")],
            # Future and pregame, but on a different slate day: wrong date, not
            # started. A past timestamp would have been bucketed as started.
            "NHL": [_discovered("NHL", "nhl-1", commence="2027-01-01T18:00:00Z")],
            "SOCCER": [
                _discovered("SOCCER", "soccer-1", status="POSTPONED"),
                _discovered("SOCCER", None),
            ],
        },
        score_row=lambda event, model: model.scorer(
            _bridge_request(event.sport), event_api=object(), canonical_hydration_required=False
        ),
    )

    assert audit["events_discovered"] == len(inventory.events) == 6
    assert audit["events_accounted"] == 6
    assert sum(audit["buckets"].values()) == 6
    assert audit["row_reconciliation"] == "PASS"
    assert audit["run_status"] == "COMPLETED"
    buckets = audit["buckets"]
    assert buckets[discovery.STARTED_OR_FINAL] == 1
    assert buckets[discovery.CANCELLED_OR_POSTPONED] == 1
    assert buckets[discovery.IDENTITY_UNRESOLVED] == 1
    assert buckets[discovery.WRONG_DATE] == 1


def test_rt_a19_an_event_that_has_started_is_removed_and_never_ranked():
    _register("MLB", lambda *a, **k: _valid_package("mlb"))
    _, rows, _ = _scan(
        {"MLB": [_discovered("MLB", "mlb-1", commence=_past_iso(), status="")]},
        score_row=lambda event, model: pytest.fail("a started event must not be scored"),
        sports=("MLB",),
    )
    assert rows[0].bucket == discovery.STARTED_OR_FINAL
    assert rows[0].rank_eligible is False
    assert rows[0].probability_publishable is False


def test_rt_a20_a_completed_model_survives_a_missing_market():
    package = {**_valid_package("mlb"), "market_evidence_status": "MARKET_DATA_UNOBTAINABLE"}
    _register("MLB", lambda *a, **k: package)
    _, rows, _ = _scan(
        {"MLB": [_discovered("MLB", "mlb-1")]},
        score_row=lambda event, model: model.scorer(
            _bridge_request(event.sport), event_api=object(), canonical_hydration_required=False
        ),
        sports=("MLB",),
    )
    row = rows[0]
    assert row.bucket == discovery.MODEL_COMPLETED
    assert row.detail["calibrated_probability"] == 0.72
    assert row.detail["calibrated_lower_bound"] == 0.66
    assert row.detail["market_evidence_status"] == "MARKET_DATA_UNOBTAINABLE"
    assert row.model_status != "MODEL_UNAVAILABLE"


def test_rt_a14_soccer_rows_are_discovered_and_keep_a_three_way_settlement_identity():
    from v17.daily_snapshot_runtime import _settlement_basis

    _, rows, audit = _scan(
        {"SOCCER": [_discovered("SOCCER", "soccer-1")]},
        score_row=lambda event, model: pytest.fail("no soccer model exists"),
        sports=("SOCCER",),
    )
    assert rows[0].identity["sport"] == "SOCCER"
    assert rows[0].model_status == "MODEL_UNAVAILABLE"
    assert audit["row_reconciliation"] == "PASS"
    # A soccer winner row settles 1X2. It must never inherit a two-way basis,
    # which is how a draw silently disappears.
    assert _settlement_basis("SOCCER") == "FULL_TIME_1X2_EXCLUDING_EXTRA_TIME"
    assert "1X2" in _settlement_basis("SOCCER")


def test_health_reports_coverage_without_trial_scoring_and_never_fakes_parity():
    _register("MLB", lambda *a, **k: _valid_package("mlb"))
    health = team_event_bridge_health()

    assert health["MLB"]["status"] == "UP"
    assert health["MLB"]["registered_capability"] is True
    for sport in ("NBA", "NHL", "SOCCER", "TENNIS", "PGA"):
        assert health[sport]["status"] == "MODEL_UNAVAILABLE"
        assert health[sport]["registered_capability"] is False
        assert health[sport]["scorer_resolvable"] is False
        assert health[sport]["discovery_supported"] is True
        assert health[sport]["reason_if_unavailable"]
        assert health[sport]["can_execute"] is False


def test_football_family_with_an_nfl_league_resolves_to_the_nfl_contract():
    # sport="FOOTBALL", league="NFL" previously normalised to the unknown sport
    # "FOOTBALL" and reported a missing model for what is really a missing alias.
    _register("NFL", lambda *a, **k: _valid_package("nfl"))
    request = SimpleNamespace(**{**vars(_bridge_request("NFL")), "sport": "FOOTBALL", "league": "NFL"})
    result = score_registered_team_event_request(request, event_api=object())
    assert result["probability_publishable"] is True


def test_a_genuinely_unknown_sport_family_still_fails_closed():
    with pytest.raises(HTTPException) as caught:
        score_registered_team_event_request(
            SimpleNamespace(
                **{**vars(_bridge_request("NFL")), "sport": "FOOTBALL", "league": "CFL"}
            ),
            event_api=object(),
        )
    assert caught.value.detail["code"] == "MODEL_UNAVAILABLE"


# ---------------------------------------------------------------------------
# RT-A07..RT-A13 — TheRundown contract, rate limits, cache
# ---------------------------------------------------------------------------

def _rundown_snapshot_payload():
    return {
        "meta": {"version": "v2"},
        "events": [
            {
                "event_id": "rd-1",
                "event_date": _slate_iso(23),
                "teams_normalized": [
                    {"name": "Chicago Cubs", "is_home": True, "is_away": False},
                    {"name": "Milwaukee Brewers", "is_home": False, "is_away": True},
                ],
                "markets": [
                    {
                        "market_id": 1,
                        "name": "moneyline",
                        "participants": [
                            {
                                "id": 101,
                                "name": "Chicago Cubs",
                                "type": "home",
                                "lines": [
                                    {"id": "ml-h", "value": 0, "prices": {"3": {"affiliate_name": "Pinnacle", "price": -135}}}
                                ],
                            },
                            {
                                "id": 102,
                                "name": "Milwaukee Brewers",
                                "type": "away",
                                "lines": [
                                    {"id": "ml-a", "value": 0, "prices": {"3": {"affiliate_name": "Pinnacle", "price": 115}}}
                                ],
                            },
                        ],
                    }
                ],
            }
        ],
    }


def _market_catalog_payload():
    return [
        {"market_id": 1, "market_name": "Moneyline", "market_display_name": "Moneyline"},
        {"market_id": 2, "market_name": "Point Spread", "market_display_name": "Spread"},
        {"market_id": 3, "market_name": "Total", "market_display_name": "Over/Under"},
    ]


def test_rt_a08_a_market_catalog_is_never_fed_to_the_odds_parser(monkeypatch):
    parser_calls: list[object] = []
    monkeypatch.setattr(
        live,
        "rundown_v2_event_to_odds_api_v4",
        lambda *a, **k: parser_calls.append(a) or None,
    )

    classification = payload_contract.classify_rundown_payload(_market_catalog_payload())
    assert classification.kind == payload_contract.MARKET_CATALOG
    assert classification.is_odds_snapshot is False

    result = live.get_sport_date_odds_snapshot(
        "baseball_mlb", SLATE_DATE, opener=_opener(_market_catalog_payload())
    )
    assert result.ok is False
    assert result.code == payload_contract.RUNDOWN_MARKET_CATALOG_NOT_ODDS_SNAPSHOT
    assert result.code != payload_contract.RUNDOWN_SCHEMA_UNRECOGNISED
    assert parser_calls == []
    diagnostics = result.schema_probe["diagnostics"]
    assert diagnostics["expected_schema"] == payload_contract.EXPECTED_ODDS_SCHEMA
    assert diagnostics["payload_python_type"] == "list"
    assert observability.counters()["rundown_market_catalog_rejected"] == 1


def test_rt_a09_a_valid_event_odds_snapshot_normalises_moneyline_outcomes():
    result = live.get_sport_date_odds_snapshot(
        "baseball_mlb", SLATE_DATE, opener=_opener(_rundown_snapshot_payload())
    )
    assert result.ok is True
    assert result.code == "MARKET_EVIDENCE_NORMALISED"
    event = result.data[0]
    assert event["home_team"] == "Chicago Cubs"
    assert event["away_team"] == "Milwaukee Brewers"
    outcomes = event["bookmakers"][0]["markets"][0]["outcomes"]
    assert {(o["name"], o["price"]) for o in outcomes} == {
        ("Chicago Cubs", -135),
        ("Milwaukee Brewers", 115),
    }
    assert event["_wow_market_evidence"]["prediction_authority"] is False
    assert result.request_audit["endpoint_family"] == "sport_date_events_snapshot"


def test_schema_diagnostics_never_leak_a_credential_or_a_price():
    result = live.get_sport_date_odds_snapshot(
        "baseball_mlb", SLATE_DATE, opener=_opener({"totally": "unexpected"})
    )
    assert result.ok is False
    assert result.code == payload_contract.RUNDOWN_SCHEMA_UNRECOGNISED
    dumped = json.dumps(result.schema_probe) + json.dumps(result.request_audit)
    assert "test-key-not-a-real-credential" not in dumped
    assert "Authorization" not in dumped


def test_rt_a10_a_burst_throttle_honours_retry_after_and_retries_within_bounds(monkeypatch):
    monkeypatch.setenv("WOW_RUNDOWN_429_MAX_ATTEMPTS", "2")
    slept: list[float] = []
    monkeypatch.setattr(live.time, "sleep", lambda seconds: slept.append(seconds))

    attempts: list[int] = []

    def opener(request, timeout=None):
        attempts.append(1)
        if len(attempts) == 1:
            raise _http_error(429, {"Retry-After": "2", "X-RateLimit-Remaining": "0"})
        return _Response(_rundown_snapshot_payload())

    result = live.get_sport_date_odds_snapshot("baseball_mlb", SLATE_DATE, opener=opener)

    assert len(attempts) == 2, "exactly one bounded retry"
    assert slept == [2.0], "the provider's Retry-After is honoured verbatim"
    assert result.ok is True
    assert observability.counters()["rundown_429_retries"] == 1


def test_rt_a10_burst_classification_preserves_every_rate_limit_header():
    limit = rate_limit.classify_rate_limit(
        {
            "Retry-After": "3",
            "X-RateLimit-Limit": "60",
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": "1789000000",
        }
    )
    assert limit.classification == rate_limit.BURST_THROTTLED
    assert limit.retry_after_seconds == 3.0
    assert limit.rate_limit == "60"
    assert limit.rate_limit_reset == "1789000000"
    assert limit.retry_allowed is True


def test_rt_a11_quota_exhaustion_is_typed_and_never_immediately_retried(monkeypatch):
    monkeypatch.setenv("WOW_RUNDOWN_429_MAX_ATTEMPTS", "3")
    monkeypatch.setattr(
        live.time, "sleep", lambda seconds: pytest.fail("quota exhaustion must not sleep and retry")
    )

    attempts: list[int] = []

    def opener(request, timeout=None):
        attempts.append(1)
        raise _http_error(
            429,
            {"X-DataPoints-Remaining": "0", "X-RateLimit-Scope": "monthly"},
            b'{"message": "You have exceeded your monthly data point quota"}',
        )

    result = live.get_sport_date_odds_snapshot("baseball_mlb", SLATE_DATE, opener=opener)

    assert len(attempts) == 1, "no retry storm on an exhausted allowance"
    assert result.ok is False
    assert result.code == rate_limit.QUOTA_EXHAUSTED
    assert result.rate_limit["datapoints_remaining"] == 0
    assert result.rate_limit["quota_scope"] == "monthly"
    assert result.rate_limit["retry_allowed"] is False
    assert observability.counters()["rundown_429_quota_exhausted"] >= 1


def test_an_unclassifiable_429_is_not_guessed_to_be_a_burst():
    limit = rate_limit.classify_rate_limit({})
    assert limit.classification == rate_limit.HTTP_429_UNKNOWN
    assert limit.retry_allowed is False
    assert rate_limit.should_retry(limit, 1) is False


def test_rt_a12_concurrent_identical_snapshot_requests_collapse_to_one_provider_call():
    calls: list[str] = []
    gate = threading.Event()

    def opener(request, timeout=None):
        calls.append(request.full_url.split("?")[0])
        gate.wait(timeout=5)
        return _Response(_rundown_snapshot_payload())

    results: list[object] = []
    errors: list[BaseException] = []

    def consume():
        try:
            results.append(
                live.get_sport_date_odds_snapshot("baseball_mlb", SLATE_DATE, opener=opener)
            )
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=consume) for _ in range(10)]
    for thread in threads:
        thread.start()
    time.sleep(0.1)
    gate.set()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    assert len(calls) == 1, "ten concurrent scorers, one provider request"
    assert len(results) == 10
    assert all(result.ok for result in results)
    payloads = {id(result.data) for result in results}
    assert len(payloads) == 1, "every consumer reads the same normalised snapshot"


def test_rt_a13_cache_keys_separate_on_every_material_request_dimension():
    base = dict(
        provider="RUNDOWN",
        capability="events",
        sport_key="baseball_mlb",
        sport_id="3",
        slate_date=SLATE_DATE,
        market_ids=("1",),
        affiliate_ids=("3",),
        main_line=True,
        hide_closed=True,
    )
    key = snapshot_cache.snapshot_key(**base)
    for field, value in (
        ("sport_key", "americanfootball_nfl"),
        ("sport_id", "2"),
        ("slate_date", "2026-09-16"),
        ("market_ids", ("2",)),
        ("affiliate_ids", ("19",)),
        ("main_line", False),
        ("hide_closed", False),
        ("capability", "openers"),
    ):
        assert snapshot_cache.snapshot_key(**{**base, field: value}) != key, field
    assert snapshot_cache.snapshot_key(**base) == key


def test_different_sports_do_not_share_a_cached_snapshot():
    calls: list[str] = []
    live.get_sport_date_odds_snapshot(
        "baseball_mlb", SLATE_DATE, opener=_opener(_rundown_snapshot_payload(), calls=calls)
    )
    live.get_sport_date_odds_snapshot(
        "americanfootball_nfl", SLATE_DATE, opener=_opener(_rundown_snapshot_payload(), calls=calls)
    )
    assert len(calls) == 2


def test_a_failed_snapshot_is_not_pinned_in_the_cache():
    calls: list[str] = []

    def failing(request, timeout=None):
        calls.append("x")
        raise _http_error(503)

    live.get_sport_date_odds_snapshot("baseball_mlb", SLATE_DATE, opener=failing)
    live.get_sport_date_odds_snapshot("baseball_mlb", SLATE_DATE, opener=failing)
    assert len(calls) == 2, "a typed failure must not be cached for the TTL"


def test_rt_a07_a_market_feed_failure_never_overwrites_a_completed_model_status():
    from v17 import llp_rundown_market_bridge as market_bridge

    package = _valid_package("mlb")

    def scorer(req, *, event_api, canonical_hydration_required=False):
        return dict(package)

    module = SimpleNamespace(score_team_event_request=scorer)
    market_bridge.install_llp_rundown_market_bridge(module)

    request = SimpleNamespace(
        requested_slate_date=SLATE_DATE,
        sport="MLB",
        league="MLB",
        home_team="Chicago Cubs",
        away_team="Milwaukee Brewers",
        official_event_id="evt-1",
        decision_intent="WINNER",
        market_prior=None,
    )
    result = module.score_team_event_request(request, event_api=object())

    assert result["calibrated_probability"] == package["calibrated_probability"]
    assert result["calibrated_lower_bound"] == package["calibrated_lower_bound"]
    assert result["model_version"] == package["model_version"]
    assert result["immutable_model_timestamp"] == package["immutable_model_timestamp"]
    assert result["llp_rundown_market_evidence"]["status"] != "EXACT_LINE"
    assert result["llp_rundown_market_evidence"]["probability_mutated_by_bridge"] is False
    for status in ("MODEL_UNAVAILABLE", "MODEL_SCORER_FAILED", "MODEL_OUTPUT_INVALID"):
        assert status not in json.dumps(
            {k: v for k, v in result.items() if k != "llp_rundown_market_evidence"}
        )


def test_market_evidence_failure_codes_are_evidence_side_not_model_side():
    result = live.get_sport_date_odds_snapshot(
        "baseball_mlb", SLATE_DATE, opener=_opener(_market_catalog_payload())
    )
    assert "MODEL_" not in str(result.code)
    assert result.prediction_authority is False
    assert result.can_execute is False


def test_winner_market_ids_are_never_guessed(monkeypatch):
    monkeypatch.delenv("WOW_RUNDOWN_WINNER_MARKET_IDS", raising=False)
    monkeypatch.delenv("WOW_RUNDOWN_MARKET_ID_MAP_JSON", raising=False)
    assert sources.rundown_winner_market_ids() == ()

    monkeypatch.setenv("WOW_RUNDOWN_MARKET_ID_MAP_JSON", json.dumps({"1": "moneyline", "2": "spread"}))
    assert sources.rundown_winner_market_ids() == ("1",)
