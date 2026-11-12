"""Governed NFL pregame feature provider: prior team state only, fail closed."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from v17 import spread_pregame_features as features_mod
from v17.spread_margin_challenger import SpreadChallengerUnavailable

START = datetime(2026, 11, 12, 23, 0, tzinfo=timezone.utc)
OBSERVED_AT = START - timedelta(hours=1)
ARTIFACT_FEATURES = ("home_prior_games", "away_prior_games")


def _event(event_id="nfl-123", *, start=START, home="DAL", away="ATL"):
    return {
        "event_id": event_id,
        "event_start_time": start.isoformat(),
        "home_team": home,
        "away_team": away,
    }


def _identity(**overrides):
    identity = {
        "ok": True,
        "canonical_event_id": "nfl-game-1",
        "canonical_home_team": "DAL",
        "canonical_away_team": "ATL",
        "identity_resolution": "PROVIDER_ID_TO_CANONICAL_SCHEDULE_MATCH",
        "canonical_source_snapshot_id": "snap-1",
        "evidence": {
            "season": 2026,
            "week": 10,
            "gameday": "2026-11-12",
            "schedule_content_sha256": "schedule-sha",
        },
    }
    identity.update(overrides)
    return identity


def _feature_row(**overrides):
    row_features = {name: 0.0 for name in features_mod.FEATURE_ORDER}
    row_features.update({"home_prior_games": 7.0, "away_prior_games": 8.0})
    row = {
        "features": row_features,
        "feature_schema_version": "NFL_P2_FEATURES_V1",
        "gameday": "2026-11-12",
        "max_prior_gameday": "2026-11-05",
        "feature_cutoff_date": "2026-11-12",
        "training_eligible": True,
        "exclusion_reasons": [],
        "home_prior_games": 7,
        "away_prior_games": 8,
        "row_inputs_hash": "row-hash-1",
    }
    row.update(overrides)
    return row


def _artifact(names=ARTIFACT_FEATURES):
    return SimpleNamespace(feature_names=tuple(names))


def _patch(monkeypatch, *, identity=None, row=None, row_error=None):
    monkeypatch.setattr(
        features_mod,
        "resolve_nfl_team_event_evidence",
        lambda request, db=None: dict(identity if identity is not None else _identity()),
    )

    def build(**_kwargs):
        if row_error is not None:
            raise row_error
        return dict(row if row is not None else _feature_row())

    monkeypatch.setattr(features_mod, "_prediction_feature_row", build)


def _rejection(monkeypatch, *, event=None, artifact=None, **patch_kwargs):
    _patch(monkeypatch, **patch_kwargs)
    with pytest.raises(SpreadChallengerUnavailable) as excinfo:
        features_mod.build_nfl_pregame_features_with_audit(
            object(), event=event or _event(), artifact=artifact or _artifact()
        )
    return excinfo.value.code


def test_features_have_the_artifact_schema_and_no_market_inputs(monkeypatch):
    _patch(monkeypatch)

    features, audit = features_mod.build_nfl_pregame_features_with_audit(
        object(), event=_event(), artifact=_artifact()
    )

    assert features == {"home_prior_games": 7.0, "away_prior_games": 8.0}
    assert audit["market_features_used"] is False
    assert audit["moneyline_probability_used"] is False
    assert audit["spread_line_used_as_feature"] is False
    assert audit["manual_probability_adjustments"] is False
    assert audit["can_execute"] is False
    assert audit["probability_publishable"] is False
    assert audit["feature_row_hash"] == "row-hash-1"
    assert audit["canonical_event_id"] == "nfl-game-1"


def test_the_row_family_is_the_p2_pregame_schema(monkeypatch):
    _patch(monkeypatch)

    features, _audit = features_mod.build_nfl_pregame_features_with_audit(
        object(), event=_event(), artifact=_artifact(names=features_mod.FEATURE_ORDER)
    )

    # The certified NFL artifact is fitted on the P2 prior-games family; with the
    # full P2 schema requested, the provider serves exactly that family.
    assert set(features) == set(features_mod.FEATURE_ORDER)
    assert all(isinstance(value, float) for value in features.values())


def test_the_evidence_request_carries_canonical_identity(monkeypatch):
    seen = {}

    def resolve(request, db=None):
        seen["request"] = request
        return _identity()

    monkeypatch.setattr(features_mod, "resolve_nfl_team_event_evidence", resolve)
    monkeypatch.setattr(features_mod, "_prediction_feature_row", lambda **_k: _feature_row())

    features_mod.build_nfl_pregame_features(object(), event=_event(), artifact=_artifact())

    request = seen["request"]
    assert request.official_event_id == "nfl-123"
    assert request.requested_slate_date == "2026-11-12"
    assert request.requested_timezone == "America/New_York"
    assert request.settlement_basis == "FULL_GAME_INCLUDING_OVERTIME"


def test_an_unresolved_event_is_inputs_insufficient(monkeypatch):
    code = _rejection(
        monkeypatch,
        identity={"ok": False, "code": "NFL_CANONICAL_EVENT_IDENTITY_MISMATCH"},
    )
    assert code == "NFL_CANONICAL_EVENT_IDENTITY_MISMATCH"


def test_an_unreadable_identity_is_typed(monkeypatch):
    def explode(request, db=None):
        raise RuntimeError("schedule snapshot table missing")

    monkeypatch.setattr(features_mod, "resolve_nfl_team_event_evidence", explode)
    with pytest.raises(SpreadChallengerUnavailable) as excinfo:
        features_mod.build_nfl_pregame_features(object(), event=_event(), artifact=_artifact())
    assert excinfo.value.code == "NFL_PREGAME_FEATURE_EVIDENCE_UNAVAILABLE:RuntimeError"


def test_a_thin_prior_sample_is_refused(monkeypatch):
    code = _rejection(
        monkeypatch, row=_feature_row(exclusion_reasons=["HOME_PRIOR_SAMPLE_THIN"])
    )
    assert code == "NFL_PREGAME_FEATURES_INSUFFICIENT:HOME_PRIOR_SAMPLE_THIN"


def test_a_missing_rate_feature_is_refused(monkeypatch):
    code = _rejection(monkeypatch, row=_feature_row(exclusion_reasons=["PRIOR_RATE_FEATURE_MISSING"]))
    assert code == "NFL_PREGAME_FEATURES_INSUFFICIENT:PRIOR_RATE_FEATURE_MISSING"


def test_temporal_leakage_is_refused_with_its_own_code(monkeypatch):
    code = _rejection(monkeypatch, row=_feature_row(exclusion_reasons=["TEMPORAL_LEAKAGE_DETECTED"]))
    assert code == "NFL_PREGAME_FEATURES_TEMPORAL_LEAKAGE"


def test_a_prior_gameday_on_the_target_day_is_leakage(monkeypatch):
    code = _rejection(monkeypatch, row=_feature_row(max_prior_gameday="2026-11-12"))
    assert code == "NFL_PREGAME_FEATURES_TEMPORAL_LEAKAGE"


def test_an_ineligible_row_is_refused(monkeypatch):
    code = _rejection(monkeypatch, row=_feature_row(training_eligible=False))
    assert code == "NFL_PREGAME_FEATURES_NOT_TRAINING_ELIGIBLE"


def test_a_feature_the_artifact_requires_but_the_row_lacks_is_a_schema_mismatch(monkeypatch):
    row = _feature_row()
    row["features"].pop("away_prior_games")

    code = _rejection(monkeypatch, row=row)

    assert code == "NFL_PREGAME_FEATURE_SCHEMA_MISMATCH:away_prior_games"


def test_a_non_finite_feature_is_refused(monkeypatch):
    row = _feature_row()
    row["features"]["away_prior_games"] = float("nan")

    code = _rejection(monkeypatch, row=row)

    assert code == "NFL_PREGAME_FEATURES_NON_FINITE:away_prior_games"


def test_a_failed_feature_row_keeps_its_typed_cause(monkeypatch):
    error = RuntimeError("no history")
    error.code = "NFL_HISTORICAL_FEATURE_CONTEXT_UNAVAILABLE"

    code = _rejection(monkeypatch, row_error=error)

    assert code == "NFL_PREGAME_FEATURE_ROW_UNAVAILABLE:NFL_HISTORICAL_FEATURE_CONTEXT_UNAVAILABLE"


def test_an_artifact_without_a_schema_is_refused(monkeypatch):
    code = _rejection(monkeypatch, artifact=_artifact(names=()))
    assert code == "NFL_PREGAME_FEATURE_ARTIFACT_SCHEMA_MISSING"


def test_an_incomplete_event_identity_is_refused(monkeypatch):
    _patch(monkeypatch)
    with pytest.raises(SpreadChallengerUnavailable) as excinfo:
        features_mod.build_nfl_pregame_features(
            object(), event={"event_id": "nfl-123", "home_team": "DAL"}, artifact=_artifact()
        )
    assert excinfo.value.code == "NFL_PREGAME_FEATURE_EVENT_IDENTITY_INCOMPLETE"


def test_a_naive_start_time_is_refused(monkeypatch):
    _patch(monkeypatch)
    with pytest.raises(SpreadChallengerUnavailable) as excinfo:
        features_mod.build_nfl_pregame_features(
            object(),
            event={"event_id": "nfl-123", "event_start_time": "2026-11-12T23:00:00",
                   "home_team": "DAL", "away_team": "ATL"},
            artifact=_artifact(),
        )
    assert excinfo.value.code == "NFL_PREGAME_FEATURE_EVENT_START_INVALID"


def test_the_producer_adapter_refuses_a_non_nfl_sport(monkeypatch):
    _patch(monkeypatch)
    with pytest.raises(SpreadChallengerUnavailable) as excinfo:
        features_mod.nfl_pregame_feature_provider(
            object(), sport="NBA", event=_event(), artifact=_artifact()
        )
    assert excinfo.value.code == "NFL_PREGAME_FEATURE_PROVIDER_SPORT_MISMATCH"


# --- producer integration -------------------------------------------------

class _DB:
    def __init__(self, market_rows=()):
        self.market_rows = list(market_rows)
        self.upserts = []
        self._mode = "read"
        self._name = ""

    def table(self, name):
        self._name = name
        self._mode = "read"
        return self

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def gte(self, *_a, **_k):
        return self

    def lte(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def upsert(self, rows, on_conflict=None, ignore_duplicates=False):
        self._mode = "write"
        self.upserts.append({"rows": [dict(row) for row in rows]})
        return self

    def execute(self):
        if self._mode == "write":
            return SimpleNamespace(data=[])
        return SimpleNamespace(data=self.market_rows)


def _market_rows(*, home_spread=-0.5, start=START):
    common = {
        "provider_event_id": "rd-nfl-9101",
        "sport_key": "americanfootball_nfl",
        "event_start_utc": start.isoformat(),
        "market_id": "2",
        "market_name": "spreads",
        "sportsbook": "Pinnacle",
        "price_updated_at": (start - timedelta(hours=2)).isoformat(),
        "fetched_at": (start - timedelta(hours=2)).isoformat(),
        "is_live": False,
        "is_main_line": True,
        "is_available": True,
    }
    return [
        {**common, "participant_type": "home", "participant_name": "Dallas Cowboys",
         "line_value": home_spread},
        {**common, "participant_type": "away", "participant_name": "Atlanta Falcons",
         "line_value": -home_spread},
    ]


def _artifact_payload():
    return {
        "sport": "NFL",
        "model_family": "NFL_SPREAD_MARGIN_RIDGE_EMPIRICAL_V1",
        "feature_schema_version": "NFL_P2_FEATURES_V1",
        "feature_names": ["home_prior_games", "away_prior_games"],
        "scaler_mean": [7.0, 8.0],
        "scaler_scale": [1.0, 1.0],
        "coefficients": [1.0, 0.0],
        "intercept": 0.0,
        "calibration_residuals": [-2.0, -1.0, 0.0, 1.0, 2.0],
        "train_rows": 100,
        "calibration_rows": 50,
        "test_rows": 50,
        "training_dataset_hash": "dataset-sha-1",
        "ridge_alpha": 4.0,
        "lifecycle_state": "CERTIFIED",
        "probability_publishable": True,
        "controlling_spread_specialist": "NFL_SPREAD_MARGIN_DISTRIBUTION_CHALLENGER_V1",
        "calibration_version": "nfl-spread-cal-v1",
    }


def test_the_provider_drives_a_complete_producer_run(monkeypatch):
    from v17.spread_receipt_producer import run_pregame_spread_production

    _patch(monkeypatch)
    db = _DB(_market_rows())

    result = run_pregame_spread_production(
        db,
        sport="NFL",
        events=[_event()],
        artifact_payload=_artifact_payload(),
        feature_provider=features_mod.nfl_pregame_feature_provider,
        observed_at=OBSERVED_AT,
    )

    assert result["status"] == "COMPLETE"
    assert result["rows"][0]["status"] == "PERSISTED"
    assert len(db.upserts) == 1
    stored = db.upserts[0]["rows"][0]
    assert stored["exact_signed_spread"] == -0.5
    assert stored["admission_status"] == "PASS"
    assert stored["can_execute"] is False


def test_a_thin_feature_sample_holds_the_row_rather_than_scoring(monkeypatch):
    from v17.spread_receipt_producer import run_pregame_spread_production

    _patch(monkeypatch, row=_feature_row(exclusion_reasons=["AWAY_PRIOR_SAMPLE_THIN"]))
    db = _DB(_market_rows())

    result = run_pregame_spread_production(
        db,
        sport="NFL",
        events=[_event()],
        artifact_payload=_artifact_payload(),
        feature_provider=features_mod.nfl_pregame_feature_provider,
        observed_at=OBSERVED_AT,
    )

    assert result["rows"][0]["status"] == "HELD"
    assert result["rows"][0]["reason_codes"] == [
        "MODEL_INPUTS_INSUFFICIENT",
        "NFL_PREGAME_FEATURES_INSUFFICIENT:AWAY_PRIOR_SAMPLE_THIN",
    ]
    assert "p_cover" not in result["rows"][0]
    assert db.upserts == []
