from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from v17 import free_first_soccer_discovery as free_first
from v17 import quota_aware_degraded_discovery as quota
from v17 import scout_secondary_source as secondary


def test_verified_soccer_targets_are_competition_specific_and_unverified_are_not_guessed():
    assert free_first.espn_sport_key("SOCCER", SimpleNamespace(league="MLS")) == "soccer_mls"
    assert free_first.espn_sport_key("SOCCER", SimpleNamespace(league="EPL")) == "soccer_epl"
    assert free_first.espn_sport_key("SOCCER", SimpleNamespace(league="UEFACHAMP")) == "soccer_uefachamp"
    assert free_first.espn_sport_key("SOCCER", SimpleNamespace(league="LIGAMX")) == "soccer_ligamx"

    # These configured targets remain on the existing provider path until their
    # public ESPN slugs are independently verified. Never guess coverage.
    assert free_first.espn_sport_key("SOCCER", SimpleNamespace(league="UEFAEURO")) is None
    assert free_first.espn_sport_key("SOCCER", SimpleNamespace(league="JPN1")) is None
    assert free_first.espn_sport_key("TENNIS", SimpleNamespace(league="ATP")) is None


def test_public_soccer_schedule_is_research_only_and_consumes_no_paid_call(monkeypatch):
    calls = []

    free_first._install_secondary_mappings(secondary)

    def fake_secondary(path, params, event_context, *, primary_failure=None):
        calls.append((path, params, event_context, primary_failure))
        assert path.endswith("/soccer_epl/events")
        return secondary.SecondaryResult(
            True,
            [{
                "id": "espn-401",
                "_wow_secondary_event_id": "401",
                "sport_key": "soccer_epl",
                "commence_time": "2026-09-25T19:00:00Z",
                "home_team": "Home FC",
                "away_team": "Away FC",
            }],
            200,
        )

    monkeypatch.setattr(secondary, "secondary_for_request", fake_secondary)
    context = quota._new_context()
    token = quota._SCAN_CONTEXT.set(context)
    try:
        ok, rows, blocker = free_first._public_soccer_schedule_fetch(
            "SOCCER",
            target=SimpleNamespace(league="EPL"),
            started=datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc),
            horizon_hours=36,
        )
    finally:
        quota._SCAN_CONTEXT.reset(token)

    assert ok is True
    assert blocker is None
    assert len(calls) == 1
    assert len(rows) == 1
    assert rows[0]["provider_event_id"] == "401"
    assert rows[0]["discovery_provider"] == "ESPN_SCOREBOARD"
    assert rows[0]["prediction_authority"] is False
    assert rows[0]["exact_line_authority"] is False
    assert rows[0]["research_only"] is True
    assert rows[0]["can_execute"] is False
    assert context["public_discovery_requests"] == 1
    assert context["public_discovery_successes"] == 1
    assert context["paid_provider_calls_saved_by_free_discovery"] == 1
    assert context["paid_provider_calls_attempted"] == 0


def test_unverified_soccer_target_falls_through_without_calling_public_source(monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("unverified target must not call ESPN with a guessed slug")

    monkeypatch.setattr(secondary, "secondary_for_request", fail_if_called)
    ok, rows, blocker = free_first._public_soccer_schedule_fetch(
        "SOCCER",
        target=SimpleNamespace(league="JPN1"),
        started=datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc),
        horizon_hours=36,
    )

    assert ok is False
    assert rows == []
    assert blocker == "PUBLIC_DISCOVERY_UNVERIFIED_TARGET"


def test_free_soccer_success_skips_wrapped_paid_odds_path(monkeypatch):
    paid_calls = []

    def original_factory(*_args, **_kwargs):
        def paid_fetch(_family, _target=None):
            paid_calls.append(1)
            return [{"id": "paid-1"}]

        return paid_fetch

    monkeypatch.setattr(
        free_first,
        "_public_soccer_schedule_fetch",
        lambda family, **kwargs: (
            True,
            [{"id": "espn-1", "prediction_authority": False, "can_execute": False}],
            None,
        ),
    )

    factory = free_first._wrap_odds_proxy_factory(original_factory)
    fetch = factory(now=datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc))
    rows = fetch("SOCCER", SimpleNamespace(league="EPL"))

    assert [row["id"] for row in rows] == ["espn-1"]
    assert paid_calls == []
    assert getattr(fetch, "_wow_schedule_first") is True


def test_soccer_union_short_circuits_remaining_paid_feeds_after_public_success():
    paid_calls = []

    def original_union_factory(*feeds):
        def fetch(family, target=None):
            rows = []
            for source in feeds:
                rows.extend(list(source(family, target) or ()))
            return rows

        return fetch

    def public_fetch(_family, _target=None):
        return [{"id": "espn-1"}]

    setattr(public_fetch, "_wow_schedule_first", True)

    def paid_fetch(_family, _target=None):
        paid_calls.append(1)
        return [{"id": "paid-1"}]

    union_factory = free_first._wrap_union_factory(original_union_factory)
    fetch = union_factory(public_fetch, paid_fetch)
    rows = fetch("SOCCER", SimpleNamespace(league="EPL"))

    assert [row["id"] for row in rows] == ["espn-1"]
    assert paid_calls == []
    assert getattr(fetch, "_wow_free_first_soccer") is True


def test_non_soccer_behavior_is_delegated_unchanged():
    def original_factory(*_args, **_kwargs):
        def fetch(family, _target=None):
            return [{"id": f"original-{family}"}]

        return fetch

    factory = free_first._wrap_odds_proxy_factory(original_factory)
    fetch = factory(now=datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc))

    assert fetch("NFL") == [{"id": "original-NFL"}]
