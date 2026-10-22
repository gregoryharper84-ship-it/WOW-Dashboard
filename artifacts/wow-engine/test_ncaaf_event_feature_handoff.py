from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace

import pytest

from ncaaf_event_feature_handoff import (
    CURRENT_CONTEXT_RECEIPT_PURPOSES,
    NCAAFEventFeatureHandoffUnavailable,
    build_current_event_feature_snapshot,
    current_event_receipt_dependencies,
    persist_current_event_feature_snapshot,
    score_current_event_snapshot,
)
from ncaaf_feature_compiler import REQUIRED_SCOPED_KEYS
from ncaaf_fitted_provider import (
    NCAAFFittedProviderUnavailable,
    RawNCAAFWinProbability,
)

KICKOFF = datetime(2026, 11, 1, 20, tzinfo=timezone.utc)
EVENT_ID = "401900001"


def _sha(payload):
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _evidence():
    rows = []
    for index, (kind, scope) in enumerate(REQUIRED_SCOPED_KEYS):
        team = {"HOME": "Louisville", "AWAY": "Florida State"}.get(scope)
        payload = {"value": 0.8 if kind == "QB_CERTAINTY" else index + 1.0}
        if kind == "REST_TRAVEL":
            payload = {"rest_days": 7.0}
            if scope == "AWAY":
                payload["travel_distance_miles"] = 120.0
        elif kind == "WEATHER":
            payload = {"wind_mph": 8.0, "precip_probability": 0.2, "temperature_f": 70.0}
        rows.append({
            "evidence_id": f"evidence-{index}",
            "official_event_id": EVENT_ID,
            "event_start_time": KICKOFF.isoformat(),
            "evidence_kind": kind,
            "scope": scope,
            "team": team,
            "source_provider": "TEST_APPROVED_NCAAF_RECEIPT",
            "source_record_id": f"source-record-{index}",
            "evidence_timestamp": (KICKOFF - timedelta(hours=2) + timedelta(seconds=index)).isoformat(),
            "payload": payload,
            "payload_sha256": _sha(payload),
            "provenance_grade": "A",
            "blocker_codes": [],
            "can_execute": False,
        })
    return rows


def _receipt(purpose, index):
    return {
        "purpose": purpose,
        "official_event_id": EVENT_ID,
        "source_provider": "TEST_OFFICIAL_NCAAF_SOURCE",
        "source_record_id": f"{purpose.lower()}-{index}",
        "evidence_timestamp": (KICKOFF - timedelta(hours=1) + timedelta(seconds=index)).isoformat(),
        "payload_sha256": _sha({"purpose": purpose, "index": index}),
    }


def _event():
    return {
        "official_event_id": EVENT_ID,
        "event_start_time": KICKOFF.isoformat(),
        "home_team": "Louisville",
        "away_team": "Florida State",
        "venue": "Cardinal Stadium",
        "neutral_site": False,
        "source_receipt": _receipt("OFFICIAL_EVENT", 1),
        "can_execute": False,
    }


def _context():
    receipts = [
        _receipt(purpose, i + 2)
        for i, purpose in enumerate(CURRENT_CONTEXT_RECEIPT_PURPOSES)
    ]
    injury_timestamp = next(
        receipt["evidence_timestamp"]
        for receipt in receipts
        if receipt["purpose"] == "INJURY_REPORT"
    )
    return {
        "starting_qb_status_home": "CONFIRMED",
        "starting_qb_status_away": "CONFIRMED",
        "depth_chart_status": "CURRENT",
        "injury_evidence_timestamp": injury_timestamp,
        "lineup_status": "REFRESHED",
        "receipts": receipts,
    }


def _snapshot():
    return build_current_event_feature_snapshot(
        _event(),
        _evidence(),
        current_context=_context(),
        now=KICKOFF - timedelta(minutes=30),
    )


def _stored_snapshot():
    return {
        **_snapshot(),
        "ok": True,
        "code": "NCAAF_EVENT_FEATURE_SNAPSHOT_READY",
        "probability_publishable": False,
        "feature_snapshot_id": "snapshot-1",
        "created_at": (KICKOFF - timedelta(minutes=35)).isoformat(),
    }


def _score_context(snapshot=None, context=None, *, refreshed_at=None):
    context = context or _context()
    normalized_receipts = [
        {
            "purpose": receipt["purpose"],
            "source_provider": receipt["source_provider"],
            "source_record_id": receipt["source_record_id"],
            "evidence_timestamp": datetime.fromisoformat(
                receipt["evidence_timestamp"].replace("Z", "+00:00")
            ).astimezone(timezone.utc).isoformat(),
            "payload_sha256": receipt["payload_sha256"],
        }
        for receipt in context["receipts"]
    ]
    injury_timestamp = datetime.fromisoformat(
        context["injury_evidence_timestamp"].replace("Z", "+00:00")
    ).astimezone(timezone.utc).isoformat()
    refresh_payload = {
        field: str(context[field]).strip()
        for field in (
            "starting_qb_status_home",
            "starting_qb_status_away",
            "depth_chart_status",
            "lineup_status",
        )
    }
    refresh_payload["injury_evidence_timestamp"] = injury_timestamp
    refresh_payload["receipts"] = normalized_receipts
    context["refresh_receipt"] = {
        "purpose": "CURRENT_CONTEXT_REFRESH",
        "official_event_id": EVENT_ID,
        "source_provider": "TEST_OFFICIAL_NCAAF_SOURCE",
        "source_record_id": "context-refresh-1",
        "evidence_timestamp": (
            refreshed_at or (KICKOFF - timedelta(minutes=32))
        ).isoformat(),
        "payload_sha256": _sha(refresh_payload),
    }
    return {
        "current_context": context,
        "now": KICKOFF - timedelta(minutes=30),
    }


class _RPC:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return SimpleNamespace(data=self.data)


class _Client:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def rpc(self, name, args):
        self.calls.append((name, args))
        return _RPC(self.data)


class _InsertQuery:
    def __init__(self, db, name):
        self.db = db
        self.name = name

    def insert(self, payload):
        self.db.inserted.append((self.name, payload))
        return self

    def execute(self):
        return SimpleNamespace(data=[{"feature_snapshot_id": "snapshot-1"}])


class _InsertDB:
    def __init__(self):
        self.inserted = []

    def table(self, name):
        return _InsertQuery(self, name)


def test_known_ncaaf_fixture_builds_and_inserts_one_immutable_market_free_snapshot():
    row = _snapshot()
    db = _InsertDB()

    receipt = persist_current_event_feature_snapshot(db, row)

    assert receipt["status"] == "PERSISTED"
    assert receipt["source_manifest_sha256"] == row["source_manifest"]["snapshot_sha256"]
    assert len(db.inserted) == 1
    assert db.inserted[0][0] == "wow_ncaaf_event_feature_snapshots"
    assert db.inserted[0][1]["market_home_no_vig"] is None
    assert db.inserted[0][1]["market_away_no_vig"] is None
    assert "feature_source_manifest" not in db.inserted[0][1]
    assert "weather_temperature_f" not in db.inserted[0][1]
    assert row["source_manifest"]["market_features_used"] is False
    assert row["feature_as_of"] < row["event_start_time"]
    assert row["can_execute"] is False
    dependencies = current_event_receipt_dependencies()
    assert len(dependencies["model_feature_evidence"]) == 29
    assert "MARKET_NO_VIG:EVENT" not in dependencies["model_feature_evidence"]
    assert dependencies["market_features"] == "PROHIBITED"
    assert dependencies["scoring_refresh_receipt"]["purpose"] == "CURRENT_CONTEXT_REFRESH"


def test_missing_current_snapshot_fails_closed():
    client = _Client({
        "ok": False,
        "code": "NCAAF_EVENT_FEATURE_SNAPSHOT_NOT_FOUND",
        "probability_publishable": False,
        "can_execute": False,
    })
    with pytest.raises(NCAAFEventFeatureHandoffUnavailable) as exc:
        score_current_event_snapshot(
            client,
            official_event_id=EVENT_ID,
            event_start_time=KICKOFF.isoformat(),
            home_team="Louisville",
            away_team="Florida State",
            expected_source_manifest_sha256="a" * 64,
            **_score_context(),
        )
    assert exc.value.code == "NCAAF_EVENT_FEATURE_SNAPSHOT_NOT_FOUND"


def test_future_evidence_is_a_typed_temporal_leakage_blocker():
    rows = _evidence()
    rows[0]["evidence_timestamp"] = KICKOFF.isoformat()
    with pytest.raises(NCAAFEventFeatureHandoffUnavailable) as exc:
        build_current_event_feature_snapshot(
            _event(), rows, current_context=_context(), now=KICKOFF - timedelta(days=1)
        )
    assert exc.value.code == "NCAAF_EVENT_FEATURE_TEMPORAL_LEAKAGE"


def test_evidence_newer_than_snapshot_as_of_is_rejected_even_before_kickoff():
    rows = _evidence()
    rows[0]["evidence_timestamp"] = (KICKOFF - timedelta(minutes=15)).isoformat()
    with pytest.raises(NCAAFEventFeatureHandoffUnavailable) as exc:
        build_current_event_feature_snapshot(
            _event(),
            rows,
            current_context=_context(),
            now=KICKOFF - timedelta(minutes=30),
        )
    assert exc.value.code == "NCAAF_EVENT_FEATURE_TEMPORAL_LEAKAGE"


def test_future_injury_timestamp_is_rejected_during_snapshot_build():
    context = _context()
    context["injury_evidence_timestamp"] = (
        KICKOFF - timedelta(minutes=15)
    ).isoformat()
    with pytest.raises(NCAAFEventFeatureHandoffUnavailable) as exc:
        build_current_event_feature_snapshot(
            _event(),
            _evidence(),
            current_context=context,
            now=KICKOFF - timedelta(minutes=30),
        )
    assert exc.value.code == "NCAAF_EVENT_FEATURE_TEMPORAL_LEAKAGE"


def test_event_receipt_must_be_bound_to_the_requested_event():
    event = _event()
    event["source_receipt"].pop("official_event_id")
    with pytest.raises(NCAAFEventFeatureHandoffUnavailable) as exc:
        build_current_event_feature_snapshot(
            event,
            _evidence(),
            current_context=_context(),
            now=KICKOFF - timedelta(minutes=30),
        )
    assert exc.value.code == "NCAAF_CURRENT_SOURCE_RECEIPT_INVALID"


def test_source_manifest_mismatch_blocks_scorer_before_invocation():
    snapshot = _snapshot()
    row = {**snapshot, "ok": True}
    client = _Client(row)
    invoked = []
    with pytest.raises(NCAAFEventFeatureHandoffUnavailable) as exc:
        score_current_event_snapshot(
            client,
            official_event_id=EVENT_ID,
            event_start_time=KICKOFF.isoformat(),
            home_team="Louisville",
            away_team="Florida State",
            expected_source_manifest_sha256="f" * 64,
            scorer=lambda *args, **kwargs: invoked.append(True),
            **_score_context(),
        )
    assert exc.value.code == "NCAAF_SOURCE_MANIFEST_MISMATCH"
    assert invoked == []


def test_row_cohort_mismatch_blocks_scorer():
    row = {**_snapshot(), "ok": True, "official_event_id": "different-event"}
    client = _Client(row)
    with pytest.raises(NCAAFEventFeatureHandoffUnavailable) as exc:
        score_current_event_snapshot(
            client,
            official_event_id=EVENT_ID,
            event_start_time=KICKOFF.isoformat(),
            home_team="Louisville",
            away_team="Florida State",
            expected_source_manifest_sha256=row["source_manifest"]["snapshot_sha256"],
            scorer=lambda *args, **kwargs: pytest.fail("cohort mismatch reached scorer"),
            **_score_context(),
        )
    assert exc.value.code == "NCAAF_EVENT_FEATURE_COHORT_MISMATCH"


@pytest.mark.parametrize(
    "current_time",
    [KICKOFF, KICKOFF + timedelta(seconds=1)],
)
def test_scoring_started_or_live_event_fails_before_database_or_scorer(current_time):
    client = _Client({})
    invoked = []
    with pytest.raises(NCAAFEventFeatureHandoffUnavailable) as exc:
        score_current_event_snapshot(
            client,
            official_event_id=EVENT_ID,
            event_start_time=KICKOFF.isoformat(),
            home_team="Louisville",
            away_team="Florida State",
            expected_source_manifest_sha256="a" * 64,
            current_context=_context(),
            now=current_time,
            scorer=lambda *args, **kwargs: invoked.append(True),
        )
    assert exc.value.code == "NCAAF_EVENT_NOT_PREGAME"
    assert client.calls == []
    assert invoked == []


def test_scoring_rechecks_kickoff_after_snapshot_read_before_scorer():
    snapshot = _stored_snapshot()
    client = _Client(snapshot)
    clock_values = iter([KICKOFF])
    invoked = []
    with pytest.raises(NCAAFEventFeatureHandoffUnavailable) as exc:
        score_current_event_snapshot(
            client,
            official_event_id=EVENT_ID,
            event_start_time=KICKOFF.isoformat(),
            home_team="Louisville",
            away_team="Florida State",
            expected_source_manifest_sha256=snapshot["source_manifest"]["snapshot_sha256"],
            current_context=_score_context(snapshot)["current_context"],
            now=KICKOFF - timedelta(minutes=30),
            clock=lambda: next(clock_values),
            scorer=lambda *args, **kwargs: invoked.append(True),
        )
    assert exc.value.code == "NCAAF_EVENT_NOT_PREGAME"
    assert len(client.calls) == 1
    assert invoked == []


def test_scoring_requires_refreshed_current_context_before_database_or_scorer():
    client = _Client({})
    invoked = []
    with pytest.raises(NCAAFEventFeatureHandoffUnavailable) as exc:
        score_current_event_snapshot(
            client,
            official_event_id=EVENT_ID,
            event_start_time=KICKOFF.isoformat(),
            home_team="Louisville",
            away_team="Florida State",
            expected_source_manifest_sha256="a" * 64,
            now=KICKOFF - timedelta(minutes=30),
            scorer=lambda *args, **kwargs: invoked.append(True),
        )
    assert exc.value.code == "NCAAF_CURRENT_EVENT_CONTEXT_REFRESH_REQUIRED"
    assert client.calls == []
    assert invoked == []


def test_scoring_rejects_context_without_a_post_snapshot_refresh_receipt():
    snapshot = _stored_snapshot()
    client = _Client(snapshot)
    invoked = []
    with pytest.raises(NCAAFEventFeatureHandoffUnavailable) as exc:
        score_current_event_snapshot(
            client,
            official_event_id=EVENT_ID,
            event_start_time=KICKOFF.isoformat(),
            home_team="Louisville",
            away_team="Florida State",
            expected_source_manifest_sha256=snapshot["source_manifest"]["snapshot_sha256"],
            current_context=_context(),
            now=KICKOFF - timedelta(minutes=30),
            scorer=lambda *args, **kwargs: invoked.append(True),
        )
    assert exc.value.code == "NCAAF_CURRENT_EVENT_CONTEXT_REFRESH_REQUIRED"
    assert invoked == []


def test_scoring_refresh_receipt_must_follow_persisted_snapshot():
    snapshot = _stored_snapshot()
    client = _Client(snapshot)
    with pytest.raises(NCAAFEventFeatureHandoffUnavailable) as exc:
        score_current_event_snapshot(
            client,
            official_event_id=EVENT_ID,
            event_start_time=KICKOFF.isoformat(),
            home_team="Louisville",
            away_team="Florida State",
            expected_source_manifest_sha256=snapshot["source_manifest"]["snapshot_sha256"],
            **_score_context(
                snapshot,
                refreshed_at=KICKOFF - timedelta(minutes=35),
            ),
        )
    assert exc.value.code == "NCAAF_CURRENT_EVENT_CONTEXT_REFRESH_REQUIRED"


def test_changed_current_qb_status_holds_before_scorer_invocation():
    snapshot = _stored_snapshot()
    client = _Client(snapshot)
    current_context = _context()
    current_context["starting_qb_status_home"] = "QUESTIONABLE"
    invoked = []
    with pytest.raises(NCAAFEventFeatureHandoffUnavailable) as exc:
        score_current_event_snapshot(
            client,
            official_event_id=EVENT_ID,
            event_start_time=KICKOFF.isoformat(),
            home_team="Louisville",
            away_team="Florida State",
            expected_source_manifest_sha256=snapshot["source_manifest"]["snapshot_sha256"],
            **_score_context(snapshot, current_context),
            scorer=lambda *args, **kwargs: invoked.append(True),
        )
    assert exc.value.code == "NCAAF_CURRENT_EVENT_CONTEXT_CHANGED"
    assert invoked == []


def test_static_wilson_diagnostic_never_becomes_a_publication_or_rank_bound():
    snapshot = _stored_snapshot()
    client = _Client(snapshot)
    raw = RawNCAAFWinProbability(
        home_probability=0.62,
        away_probability=0.38,
        model_artifact_version="certified-artifact-1",
        artifact_id="artifact-1",
    )
    calibrated = SimpleNamespace(
        calibrated_probability=0.60,
        calibration_lower_diagnostic=0.41,
        calibration_upper_diagnostic=0.75,
    )
    result = score_current_event_snapshot(
        client,
        official_event_id=EVENT_ID,
        event_start_time=KICKOFF.isoformat(),
        home_team="Louisville",
        away_team="Florida State",
        expected_source_manifest_sha256=snapshot["source_manifest"]["snapshot_sha256"],
        scorer=lambda *args, **kwargs: raw,
        calibrator=lambda *args, **kwargs: calibrated,
        **_score_context(snapshot),
    )
    assert result["static_calibration_lower_diagnostic"] == 0.41
    assert result["calibrated_lower_bound"] is None
    assert result["rank_eligible"] is False
    assert result["probability_publishable"] is False
    assert "NCAAF_CERTIFIED_DYNAMIC_BOUND_NOT_AVAILABLE" in result["blockers"]
    assert result["can_execute"] is False


def test_invoked_scorer_failure_is_not_collapsed_to_model_unavailable():
    snapshot = _stored_snapshot()
    client = _Client(snapshot)

    def fail(*_args, **_kwargs):
        raise RuntimeError("scorer service failed")

    with pytest.raises(NCAAFEventFeatureHandoffUnavailable) as exc:
        score_current_event_snapshot(
            client,
            official_event_id=EVENT_ID,
            event_start_time=KICKOFF.isoformat(),
            home_team="Louisville",
            away_team="Florida State",
            expected_source_manifest_sha256=snapshot["source_manifest"]["snapshot_sha256"],
            scorer=fail,
            **_score_context(snapshot),
        )
    assert exc.value.code == "MODEL_SCORER_FAILED"


def test_typed_invalid_model_output_keeps_output_failure_semantics():
    snapshot = _stored_snapshot()
    client = _Client(snapshot)

    def invalid_output(*_args, **_kwargs):
        raise NCAAFFittedProviderUnavailable(
            "NCAAF_MODEL_OUTPUT_NOT_NORMALIZED", "invalid model probabilities"
        )

    with pytest.raises(NCAAFEventFeatureHandoffUnavailable) as exc:
        score_current_event_snapshot(
            client,
            official_event_id=EVENT_ID,
            event_start_time=KICKOFF.isoformat(),
            home_team="Louisville",
            away_team="Florida State",
            expected_source_manifest_sha256=snapshot["source_manifest"]["snapshot_sha256"],
            scorer=invalid_output,
            **_score_context(snapshot),
        )
    assert exc.value.code == "MODEL_OUTPUT_INVALID"
