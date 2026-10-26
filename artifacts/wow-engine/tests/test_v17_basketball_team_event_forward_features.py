from __future__ import annotations

from datetime import date
from copy import deepcopy
from types import SimpleNamespace

import pytest

from v17.basketball_team_event_forward_features import (
    BasketballForwardFeatureError,
    build_basketball_forward_features,
    bind_nba_frozen_source_to_features,
    hydrate_current_basketball_features,
)
from basketball_team_event_specialist import FEATURE_NAMES, FEATURE_SCHEMA_VERSION


def _game(gid, day, home, away, hs, aas):
    return SimpleNamespace(
        game_id=gid,
        game_date=date.fromisoformat(day),
        home_team_id=home,
        away_team_id=away,
        home_score=hs,
        away_score=aas,
        home_win=hs > aas,
    )


def _history():
    rows = []
    for index, day in enumerate(("2026-09-01","2026-09-03","2026-09-05","2026-09-07","2026-09-09","2026-09-11")):
        rows.append(_game(f"h{index}", day, "espn-1", f"opp-h-{index}", 100 + index, 90))
        rows.append(_game(f"a{index}", day, f"opp-a-{index}", "espn-2", 95, 98 + index))
    return rows


def test_forward_features_match_exact_fitted_schema_and_are_pregame_only():
    rows = _history()
    rows.append(_game("future", "2026-10-08", "espn-1", "espn-2", 200, 10))
    features, audit = build_basketball_forward_features(
        rows,
        target_date=date(2026, 10, 7),
        home_team_id="espn-1",
        away_team_id="espn-2",
    )
    assert tuple(features) == tuple(FEATURE_NAMES)
    assert audit["feature_schema_version"] == FEATURE_SCHEMA_VERSION
    assert audit["home_prior_games"] == 6
    assert audit["away_prior_games"] == 6
    assert audit["market_features_used"] is False
    assert audit["market_probability_used_as_model"] is False
    assert audit["can_execute"] is False
    assert len(audit["prior_history_sha256"]) == 64
    assert audit["prior_history_events"] == 12
    assert audit["home_history_age_days"] == 26
    assert audit["away_history_age_days"] == 26


def test_forward_features_fail_closed_when_history_is_thin():
    with pytest.raises(BasketballForwardFeatureError) as exc:
        build_basketball_forward_features(
            _history()[:4],
            target_date=date(2026, 10, 7),
            home_team_id="espn-1",
            away_team_id="espn-2",
        )
    assert exc.value.code == "BASKETBALL_FORWARD_HISTORY_INSUFFICIENT"


def test_nba_hydration_uses_canonical_identity_and_never_publishes(monkeypatch):
    monkeypatch.setattr(
        "v17.basketball_team_event_forward_features.load_games",
        lambda _db, sport: _history(),
    )

    def resolver(**_kwargs):
        return {
            "event_id": "espn-401999999",
            "home_team_id": "espn-1",
            "away_team_id": "espn-2",
            "identity_provider": "SPORTSDATAVERSE_ESPN",
            "identity_resolution": "SPORTSDATAVERSE_SCHEDULE_EXACT_TEAM_DATE_MATCH",
            "can_execute": False,
        }

    out = hydrate_current_basketball_features(
        object(),
        sport="NBA",
        event_start_time="2026-10-07T23:00:00Z",
        home_team_alias="espn-1",
        away_team_alias="espn-2",
        nba_identity_resolver=resolver,
    )
    assert out["status"] == "PASS"
    assert out["official_event_id"] == "espn-401999999"
    assert out["feature_schema_version"] == FEATURE_SCHEMA_VERSION
    assert tuple(out["features"]) == tuple(FEATURE_NAMES)
    assert out["probability_publishable"] is False
    assert out["rank_eligible"] is False
    assert out["can_execute"] is False


def test_wnba_hydration_requires_exact_event_identity(monkeypatch):
    monkeypatch.setattr(
        "v17.basketball_team_event_forward_features.load_games",
        lambda _db, sport: _history(),
    )
    with pytest.raises(BasketballForwardFeatureError) as exc:
        hydrate_current_basketball_features(
            object(),
            sport="WNBA",
            event_start_time="2026-10-08T00:00:00Z",
            home_team_alias="espn-1",
            away_team_alias="espn-2",
        )
    assert exc.value.code == "WNBA_EVENT_ID_REQUIRED"


def test_history_receipt_is_deterministic_and_excludes_unrelated_or_future_results():
    rows = _history()
    date_of_game = date(2026, 10, 7)
    kwargs = {"target_date": date_of_game, "home_team_id": "espn-1", "away_team_id": "espn-2"}
    base, audit = build_basketball_forward_features(rows, **kwargs)
    # Neither provider pagination order nor irrelevant/after-event rows can
    # change the exact-input digest or sporting feature vector.
    extra = [
        _game("not-our-event", "2026-09-03", "espn-77", "espn-88", 140, 10),
        _game("after-target", "2026-10-08", "espn-1", "espn-2", 200, 1),
    ]
    other, other_audit = build_basketball_forward_features(list(reversed(rows)) + extra, **kwargs)
    assert other == base
    assert other_audit["prior_history_sha256"] == audit["prior_history_sha256"]
    assert other_audit["prior_history_events"] == 12
    # A changed settled record is captured even if the model's aggregate
    # feature vector happens to stay unchanged.
    replacement = _game("h0", "2026-09-01", "espn-1", "opp-h-0", 101, 90)
    modified = [replacement if row.game_id == "h0" else row for row in rows]
    _, modified_audit = build_basketball_forward_features(modified, **kwargs)
    assert modified_audit["prior_history_sha256"] != audit["prior_history_sha256"]


def test_duplicate_history_event_id_cannot_inflate_prior_games():
    rows = _history()
    with pytest.raises(BasketballForwardFeatureError) as exc:
        build_basketball_forward_features(
            rows + [rows[0]], target_date=date(2026, 10, 7),
            home_team_id="espn-1", away_team_id="espn-2",
        )
    assert exc.value.code == "BASKETBALL_FORWARD_HISTORY_IDENTITY_DUPLICATE_OR_MISSING"


def _raw_source_binding_case():
    features, audit = build_basketball_forward_features(
        _history(), target_date=date(2026, 10, 7),
        home_team_id="espn-1", away_team_id="espn-2",
    )
    package = {
        "status": "PASS", "sport": "NBA",
        "official_event_id": "espn-401999999",
        "event_start_time_utc": "2026-10-07T23:00:00+00:00",
        "home_team_id": "espn-1", "away_team_id": "espn-2",
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "features": features, "feature_audit": audit,
        "probability_publishable": False, "rank_eligible": False, "can_execute": False,
    }
    manifest = {
        "sport": "NBA", "disposition": "RAW_SOURCE_CAPTURE_ONLY",
        "source_http_date": "2026-10-07T19:30:00+00:00",
        "capture_host_clock_utc": "2026-10-07T19:31:00+00:00",
        "raw_sha256": "a" * 64, "independent_timestamp_attestation": "NOT_ESTABLISHED",
        "events_pregame_eligible": [{
            "event_id": "401999999", "event_start_utc": "2026-10-07T23:00:00Z",
            "state": "pre",
            "home": {"team_id": "1", "name": "Home"},
            "away": {"team_id": "2", "name": "Away"},
        }],
    }
    replay = {
        "status": "RAW_CAPTURE_HASH_AND_EVENT_REPLAY_PASS", "raw_sha256": "a" * 64,
        "independent_timestamp_attestation": "NOT_ESTABLISHED",
    }
    return package, manifest, replay


def test_exact_espn_raw_capture_can_bind_only_to_matching_pregame_history():
    package, manifest, replay = _raw_source_binding_case()
    source_copy, manifest_copy = deepcopy(package), deepcopy(manifest)
    out = bind_nba_frozen_source_to_features(
        package, capture_manifest=manifest, replay_receipt=replay,
        feature_freeze_timestamp="2026-10-07T20:00:00Z",
    )
    assert out["source_snapshot_id"] == "sha256:" + "a" * 64
    assert out["source_snapshot_timestamp"] == "2026-10-07T19:30:00+00:00"
    assert out["features"] == package["features"]
    assert out["feature_audit"]["dataset_id"] == "sha256:" + package["feature_audit"]["prior_history_sha256"]
    assert out["feature_audit"]["source_event_id"] == "espn-401999999"
    assert out["feature_audit"]["timestamp_attestation"] == "NOT_ESTABLISHED"
    assert out["probability_publishable"] is False
    assert out["rank_eligible"] is False
    assert out["can_execute"] is False
    assert package == source_copy and manifest == manifest_copy


@pytest.mark.parametrize(("mutation", "expected_code"), [
    ("wrong_raw_hash", "NBA_SOURCE_EVENT_REPLAY_NOT_VERIFIED"),
    ("malformed_raw_hash", "NBA_RAW_SOURCE_SHA256_INVALID"),
    ("duplicate_event", "NBA_CAPTURE_CANONICAL_EVENT_NOT_UNIQUE"),
    ("swapped_home", "NBA_CAPTURE_EVENT_IDENTITY_MISMATCH"),
    ("wrong_start", "NBA_CAPTURE_EVENT_IDENTITY_MISMATCH"),
    ("wrong_sport", "NBA_RAW_SOURCE_CAPTURE_INVALID"),
    ("not_pregame", "NBA_CAPTURE_EVENT_IDENTITY_MISMATCH"),
    ("after_kickoff", "NBA_FROZEN_SOURCE_NOT_PREGAME"),
    ("source_after_capture", "NBA_FROZEN_SOURCE_NOT_PREGAME"),
    ("clock_skew", "NBA_SOURCE_CLOCK_SKEW_EXCEEDED"),
    ("missing_history_hash", "NBA_FEATURE_LINEAGE_MISSING"),
])
def test_raw_nba_capture_binding_rejects_contradictory_or_fabricated_evidence(mutation, expected_code):
    package, manifest, replay = _raw_source_binding_case()
    freeze = "2026-10-07T20:00:00Z"
    event = manifest["events_pregame_eligible"][0]
    if mutation == "wrong_raw_hash":
        replay["raw_sha256"] = "f" * 64
    elif mutation == "malformed_raw_hash":
        manifest["raw_sha256"] = "not-a-sha"
    elif mutation == "duplicate_event":
        manifest["events_pregame_eligible"].append(deepcopy(event))
    elif mutation == "swapped_home":
        event["home"]["team_id"] = "2"
    elif mutation == "wrong_start":
        event["event_start_utc"] = "2026-10-08T02:00:00Z"
    elif mutation == "wrong_sport":
        manifest["sport"] = "WNBA"
    elif mutation == "not_pregame":
        event["state"] = "post"
    elif mutation == "after_kickoff":
        freeze = "2026-10-07T23:00:01Z"
    elif mutation == "source_after_capture":
        manifest["source_http_date"] = "2026-10-07T19:32:00Z"
    elif mutation == "clock_skew":
        manifest["capture_host_clock_utc"] = "2026-10-07T19:36:00Z"
    elif mutation == "missing_history_hash":
        del package["feature_audit"]["prior_history_sha256"]
    with pytest.raises(BasketballForwardFeatureError) as exc:
        bind_nba_frozen_source_to_features(
            package, capture_manifest=manifest, replay_receipt=replay,
            feature_freeze_timestamp=freeze,
        )
    assert exc.value.code == expected_code
