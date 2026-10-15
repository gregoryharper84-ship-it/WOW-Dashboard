from __future__ import annotations

from v17 import cross_sport_winner_discovery as discovery
from v17.cross_sport_acquisition_fairness import fair_discover_winner_slate


def test_provider_failure_cannot_starve_later_configured_families() -> None:
    families = ("MLB", "WNBA", "NHL", "SOCCER", "TENNIS", "MMA")
    targets = {
        family: (
            discovery.DiscoveryTarget(
                family=family,
                provider="TEST_PROVIDER",
                league=f"{family}_LEAGUE",
                sport_key=f"{family.lower()}_key",
            ),
        )
        for family in families
    }
    attempts: list[str] = []

    def failing_fetch(family, target):
        attempts.append(family)
        raise discovery.DiscoveryFeedError("HTTP_429")

    inventory = fair_discover_winner_slate(
        requested_slate_date="2026-10-02",
        requested_timezone="America/Chicago",
        fetch_sport_events=failing_fetch,
        supported_sports=families,
        discovery_targets=targets,
        budget_seconds=0.0,
    )

    assert attempts == list(families)
    assert inventory.sports_queried == list(families)
    assert len(inventory.acquisition_audit) == len(families)
    assert len(inventory.acquisition_details) == len(families)
    assert len(inventory.source_blockers) == len(families)
    assert inventory.events == []
    assert all(
        row["request_status"] == discovery.PROVIDER_RATE_LIMITED
        for row in inventory.acquisition_audit
    )
    assert all(
        row["coverage_complete"] is True
        and row["first_pass_attempted"] is True
        and row["can_execute"] is False
        for row in inventory.acquisition_audit
    )
    assert all(
        blocker["status"] == discovery.PROVIDER_RATE_LIMITED
        for blocker in inventory.source_blockers
    )


def test_families_without_public_schedule_report_typed_unsupported_state() -> None:
    from datetime import datetime, timezone

    from v17 import cross_sport_resilience_overlay as resilience
    from v17 import quota_aware_degraded_discovery as quota_aware

    started = datetime(2026, 10, 13, tzinfo=timezone.utc)
    for family in ("SOCCER", "TENNIS", "MMA", "PGA", "BOXING", "CRICKET"):
        assert family not in resilience._ESPN_FAMILY_KEYS
        ok, rows, code = quota_aware._public_schedule_fetch(
            family, started=started, horizon_hours=24
        )
        assert (ok, rows) == (False, [])
        assert code == "PUBLIC_DISCOVERY_UNSUPPORTED_FOR_FAMILY"
        assert code != "MODEL_UNAVAILABLE"
    assert resilience.CAN_EXECUTE is False


# ---------------------------------------------------------------------------
# Oct 14 production evidence (run v17-daily-3bf42530-..., 34 targets):
# SOCCER/TENNIS/MMA/PGA/CRICKET failed as ODDS_PROVIDER_NON_JSON with
# upstream_status=null while wow-odds-router logged HTTP 429 for every
# /odds-api/v4/sports?all=true call; persistence still reported
# board_completeness=True.
# ---------------------------------------------------------------------------
import io
import json
from datetime import datetime as _dt, timedelta as _td, timezone as _tz
from urllib.error import HTTPError

import pytest

from v17 import cross_sport_discovery_feed as feed
from v17 import daily_snapshot_runtime as runtime
from v17 import nightly_multiscout as scout
from v17 import nightly_multiscout_oidc as scout_oidc
from v17 import quota_aware_degraded_discovery as quota_aware
from v17.daily_response_contract import (
    DAILY_ACQUISITION_DETAIL_TABLE,
    MAX_ACQUISITION_FAMILY_SUMMARY,
    persist_acquisition_detail,
    summarize_acquisition_by_family,
)


def _http_error(status: int, body: dict | str):
    raw = body if isinstance(body, str) else json.dumps(body)
    return HTTPError("https://router.invalid/odds-api/v4/sports", status, "err", {}, io.BytesIO(raw.encode()))


def _proxy_result(monkeypatch, status, body):
    monkeypatch.setenv("WOW_ODDS_PROXY_ACTION_KEY", "test-token-not-secret")

    def raise_error(*_args, **_kwargs):
        raise _http_error(status, body)

    monkeypatch.setattr(scout, "urlopen", raise_error)
    return scout.proxy_get("/odds-api/v4/sports", {"all": "true"})


def test_router_non_json_429_is_typed_rate_limited_with_status(monkeypatch) -> None:
    result = _proxy_result(monkeypatch, 429, {"ok": False, "code": "ODDS_PROVIDER_NON_JSON", "can_execute": False})
    assert result.ok is False
    assert result.code == "ODDS_PROVIDER_HTTP_429"
    assert result.upstream_status == 429 and result.status == 429
    assert discovery.classify_acquisition_failure(result.code) == discovery.PROVIDER_RATE_LIMITED
    # Rate limiting is not a definitive circuit state: same circuit behaviour as before.
    assert quota_aware.classify_provider_failure(result.code) == quota_aware.RATE_LIMITED
    assert scout_oidc._eligible_for_secondary(result) is True


def test_proxy_upstream_non_json_429_in_body_is_typed_rate_limited(monkeypatch) -> None:
    body = {"detail": {"code": "ODDS_API_UPSTREAM_NON_JSON", "upstream_status": 429, "content_type_class": "TEXT_HTML"}}
    result = _proxy_result(monkeypatch, 502, body)
    assert result.code == "ODDS_PROVIDER_HTTP_429"
    assert result.upstream_status == 429 and result.content_type_class == "TEXT_HTML"
    assert scout_oidc._eligible_for_secondary(result) is True


@pytest.mark.parametrize("status,body,code,upstream", [
    (502, {"ok": False, "code": "ODDS_PROVIDER_NON_JSON"}, "ODDS_PROVIDER_NON_JSON", 502),
    (403, {"ok": False, "code": "ODDS_PROVIDER_NON_JSON"}, "ODDS_PROVIDER_NON_JSON", 403),
    (502, {"detail": {"code": "ODDS_API_UPSTREAM_NON_JSON", "upstream_status": 200,
                      "content_type_class": "TEXT_HTML"}}, "ODDS_API_UPSTREAM_NON_JSON", 200),
    (401, {"detail": {"code": "ODDS_ROUTER_AUTH_INVALID"}}, "ODDS_ROUTER_AUTH_INVALID", None),
    (429, "<html>busy</html>", "HTTP_429", None),
])
def test_non_429_failures_keep_their_code_and_only_gain_status(monkeypatch, status, body, code, upstream) -> None:
    result = _proxy_result(monkeypatch, status, body)
    assert result.code == code
    assert result.upstream_status == upstream
    assert result.code != "MODEL_UNAVAILABLE"


def test_feed_failure_record_keeps_upstream_status() -> None:
    fetched = scout.FetchResult(False, status=429, code="ODDS_PROVIDER_HTTP_429", upstream_status=429)
    acquisition = feed._odds_failure_result(fetched, fetched.code)
    assert acquisition.primary_upstream_status == 429
    assert acquisition.attempts[0].upstream_status == 429
    assert acquisition.blocker_code == "ODDS_PROVIDER_HTTP_429"


# --- board completeness can never be reported for a partial board ----------

class _Res:
    def __init__(self, data):
        self.data = data


class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.run_id, self.lo, self.hi = db, name, None, 0, 10_000

    def select(self, *_a, **_k): return self
    def order(self, *_a, **_k): return self
    def eq(self, col, val):
        if col == "run_id":
            self.run_id = val
        return self
    def limit(self, n): self.hi = int(n) - 1; return self
    def range(self, lo, hi): self.lo, self.hi = int(lo), int(hi); return self
    def upsert(self, payload, *_a, **_k):
        for row in payload:
            self.db.rows[(row["run_id"], row["family"], row["target_key"])] = dict(row)
        return self
    def execute(self):
        if self.name != DAILY_ACQUISITION_DETAIL_TABLE:
            return _Res([])
        rows = sorted((r for r in self.db.rows.values() if self.run_id in (None, r["run_id"])),
                      key=lambda r: (r["family"], r["target_key"]))
        return _Res(rows[self.lo:self.hi + 1])


class _FakeDB:
    def __init__(self):
        self.rows = {}

    def table(self, name):
        return _Q(self, name)


def _detail(family, key, final_state, events=0, blocker=None, upstream=None):
    ok = final_state in ("EVENTS_RETURNED", "NO_EVENTS_RETURNED")
    return {
        "family": family, "target_key": key, "provider": "TEST_PROVIDER", "league": family,
        "regime": "REGULAR_SEASON", "provider_sport_id": None, "sport_key": None,
        "final_state": final_state,
        "provider_status": "PROVIDER_SUCCEEDED" if ok else "PROVIDER_FAILED",
        "fallback_status": "FALLBACK_NOT_APPLICABLE",
        "exhaustion_status": "PATHS_NOT_EXHAUSTED" if ok else "PROVIDER_PATHS_EXHAUSTED",
        "events_returned": events, "duplicate_rows_suppressed": 0, "blocker_code": blocker,
        "primary_path_id": "ESPN_SCOREBOARD" if ok else "ODDS_PROXY",
        "primary_upstream_status": upstream,
        "can_execute": False,
    }


OCT14_SHAPE = [
    _detail("MLB", "ESPN|MLB", "EVENTS_RETURNED", events=3),
    _detail("NBA", "ESPN|NBA", "NO_EVENTS_RETURNED"),
    _detail("SOCCER", "RUNDOWN|10|MLS|REGULAR_SEASON", "PROVIDER_RATE_LIMITED",
            blocker="ODDS_PROVIDER_HTTP_429:detail-not-persisted", upstream=429),
    _detail("SOCCER", "RUNDOWN|11|EPL|REGULAR_SEASON", "PROVIDER_REQUEST_FAILED", blocker="ODDS_PROXY_CIRCUIT_OPEN"),
    _detail("BOXING", "NO_CONFIGURED_DISCOVERY_FEED", "NO_CONFIGURED_DISCOVERY_FEED", blocker="NO_CONFIGURED_DISCOVERY_FEED"),
]


def _expected(details):
    return [{"family": d["family"], "target_key": d["target_key"]} for d in details]


def test_partial_board_is_accounted_but_never_complete() -> None:
    result = persist_acquisition_detail(_FakeDB(), run_id="oct14", details=OCT14_SHAPE,
                                        expected_targets=_expected(OCT14_SHAPE))
    assert result["detail_available"] is True and result["detail_reconciliation"] is True
    assert result["board_completeness"] is False
    assert result["incomplete_target_count"] == 3
    assert result["incomplete_families"] == ["BOXING", "SOCCER"]
    assert result["blockers"] == []  # truthful partial board, not a persistence failure


def test_fully_answered_board_is_complete() -> None:
    details = OCT14_SHAPE[:2]
    result = persist_acquisition_detail(_FakeDB(), run_id="clean", details=details,
                                        expected_targets=_expected(details))
    assert result["board_completeness"] is True and result["incomplete_target_count"] == 0


@pytest.mark.parametrize("state", [
    "PROVIDER_RATE_LIMITED", "PROVIDER_REQUEST_FAILED", "PROVIDER_SCHEMA_FAILURE",
    "DISCOVERY_BUDGET_EXHAUSTED", "NO_CONFIGURED_DISCOVERY_FEED", "SOMETHING_NEW",
])
def test_any_single_failed_target_blocks_completeness(state) -> None:
    details = [OCT14_SHAPE[0], _detail("NHL", "ESPN|NHL", state, blocker="X")]
    result = persist_acquisition_detail(_FakeDB(), run_id=f"r-{state}", details=details,
                                        expected_targets=_expected(details))
    assert result["board_completeness"] is False


def test_family_summary_is_bounded_sanitized_and_typed() -> None:
    summary = {row["family"]: row for row in summarize_acquisition_by_family(OCT14_SHAPE)}
    assert summary["MLB"]["status"] == "COMPLETE" and summary["MLB"]["events_returned"] == 3
    soccer = summary["SOCCER"]
    assert soccer["status"] == "FAILED" and soccer["targets"] == 2 and soccer["targets_incomplete"] == 2
    assert soccer["first_failure"]["blocker_code"] == "ODDS_PROVIDER_HTTP_429"
    assert soccer["first_failure"]["primary_upstream_status"] == 429
    assert summary["BOXING"]["first_failure"]["final_state"] == "NO_CONFIGURED_DISCOVERY_FEED"
    text = json.dumps(list(summary.values()))
    assert "detail-not-persisted" not in text and "probability" not in text and "price" not in text
    many = [_detail(f"F{i:02d}", f"K{i}", "EVENTS_RETURNED") for i in range(MAX_ACQUISITION_FAMILY_SUMMARY + 10)]
    assert len(summarize_acquisition_by_family(many)) == MAX_ACQUISITION_FAMILY_SUMMARY
    assert summarize_acquisition_by_family(None) == []


def _completed_row():
    return {
        "lane": "MONEYLINE", "identity": {"sport": "NFL", "official_event_id": "nfl-1"},
        "terminal": True, "row_status": "COMPLETED", "probability_publishable": True,
        "result": {"calibrated_probability": 0.62, "calibrated_lower_bound": 0.57,
                   "probability_publishable": True, "rank_eligible": True, "can_execute": False},
        "terminal_reduction": {"lowest_stage_terminal": "COMPLETED", "final_terminal": "COMPLETED",
                               "terminal_upgraded_from_rejection": False, "can_execute": False},
        "can_execute": False,
    }


@pytest.mark.parametrize("mode", ["COMPACT", "FULL"])
def test_daily_run_reports_incomplete_board_and_per_sport_receipt(monkeypatch, mode) -> None:
    packet = {
        "discovery": {"sports_queried": ["MLB", "NBA", "SOCCER", "BOXING"]},
        "rows": [], "reconciliation": {"row_reconciliation": "PASS"},
        "acquisition_details": [dict(d) for d in OCT14_SHAPE],
        "expected_acquisition_targets": _expected(OCT14_SHAPE),
        "can_execute": False,
    }
    monkeypatch.setattr(runtime, "_cross_sport_moneyline_rows", lambda *_a, **_k: ([_completed_row()], packet))
    payload = runtime.run_daily_snapshot(
        runtime.DailySnapshotRequest(
            requested_slate_date=(_dt.now(_tz.utc) + _td(days=1)).date().isoformat(),
            requested_timezone="UTC", lanes=["MONEYLINE"], response_mode=mode,
        ),
        db=_FakeDB(), market_api=object(), event_api=object(),
    )
    audit = payload["cross_sport_discovery_audit"]
    assert audit["reconciliation"]["board_completeness"] == "INCOMPLETE"
    assert audit["reconciliation"]["acquisition_detail_reconciliation"] == "PASS"
    families = {row["family"]: row["status"] for row in audit["acquisition_family_summary"]}
    assert families == {"BOXING": "FAILED", "MLB": "COMPLETE", "NBA": "COMPLETE", "SOCCER": "FAILED"}
    assert not any("ACQUISITION" in str(b) for b in payload.get("blockers") or [])
    # Probability rows are untouched by acquisition truthfulness.
    if mode == "FULL":
        assert payload["rows"][0]["result"]["calibrated_probability"] == 0.62
    assert payload["can_execute"] is False


@pytest.mark.parametrize("body", [
    {"message": "key deactivated", "error_code": "DEACTIVATED_KEY"},
    {"ok": False, "code": "DEACTIVATED_KEY"},
    {"detail": {"code": "DEACTIVATED_KEY"}},
])
def test_deactivated_odds_key_is_typed_credential_failure(monkeypatch, body) -> None:
    result = _proxy_result(monkeypatch, 401, body)
    assert result.ok is False and result.status == 401
    assert result.code == "ODDS_API_KEY_DEACTIVATED"
    assert not result.code.startswith("MODEL_")
    assert scout_oidc._eligible_for_secondary(result) is True
    assert scout_oidc._is_transient(result) is False
    assert "test-token-not-secret" not in repr(result)


@pytest.mark.parametrize("status,body", [
    (401, {"detail": {"code": "ODDS_ROUTER_AUTH_INVALID"}}),
    (401, {"detail": {"code": "ODDS_PROXY_AUTH_INVALID", "error_code": "DEACTIVATED_KEY"}}),
    (401, {"message": "unauthorized"}),
    (403, {"error_code": "DEACTIVATED_KEY"}),
])
def test_deactivated_key_typing_does_not_loosen_caller_auth_or_generic_failures(monkeypatch, status, body) -> None:
    result = _proxy_result(monkeypatch, status, body)
    assert result.code != "ODDS_API_KEY_DEACTIVATED"
    assert scout_oidc._eligible_for_secondary(result) is False
