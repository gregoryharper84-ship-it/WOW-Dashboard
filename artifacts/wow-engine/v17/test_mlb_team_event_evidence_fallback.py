from datetime import datetime, timedelta, timezone

from v17.mlb_team_event_hydration import resolve_mlb_team_event_evidence


class _Req:
    official_event_id = "mlb-test-1"
    home_team = "Chicago Cubs"
    away_team = "Pittsburgh Pirates"
    event_start_time_utc = (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()
    source_snapshot_id = "betus-visible-board-20260912-001"
    latest_material_update_timestamp = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    sport_specific_evidence = {
        "venue": "Wrigley Field",
        "home_starting_pitcher": "Cubs Starter",
        "away_starting_pitcher": "Pirates Starter",
    }


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_a, **_k): return self
    def eq(self, *_a, **_k): return self
    def order(self, *_a, **_k): return self
    def limit(self, *_a, **_k): return self
    def execute(self): return _Result(self.rows)


class _Client:
    def __init__(self, rows):
        self.rows = rows

    def table(self, _name):
        return _Query(self.rows)


class _Api:
    def __init__(self, rows):
        self.client = _Client(rows)

    def get_client(self):
        return self.client


def test_complete_timestamped_caller_evidence_fills_missing_canonical_snapshot():
    result = resolve_mlb_team_event_evidence(_Req(), event_api=_Api([]))
    assert result["ok"] is True
    assert result["code"] == "MLB_TEAM_EVENT_CALLER_EVIDENCE_FALLBACK_READY"
    assert result["fallback_reason"] == "MLB_TEAM_EVENT_CANONICAL_SNAPSHOT_UNAVAILABLE"
    assert result["evidence"]["venue"] == "Wrigley Field"
    assert result["evidence"]["home_starting_pitcher"] == "Cubs Starter"
    assert result["evidence"]["away_starting_pitcher"] == "Pirates Starter"
    assert result["evidence"]["home_starter_status"] == "PROBABLE"
    assert result["evidence"]["home_lineup_status"] == "PROJECTED"
    assert result["evidence_authority"] == "EXPLICIT_REQUEST_FALLBACK"
    assert result["can_execute"] is False


def test_incomplete_caller_evidence_does_not_bypass_missing_canonical_snapshot():
    req = _Req()
    req.sport_specific_evidence = {"venue": "Wrigley Field"}
    result = resolve_mlb_team_event_evidence(req, event_api=_Api([]))
    assert result["ok"] is False
    assert result["code"] == "MLB_TEAM_EVENT_CANONICAL_SNAPSHOT_UNAVAILABLE"


def test_future_dated_caller_snapshot_is_rejected():
    req = _Req()
    req.latest_material_update_timestamp = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    result = resolve_mlb_team_event_evidence(req, event_api=_Api([]))
    assert result["ok"] is False
    assert result["code"] == "MLB_TEAM_EVENT_CANONICAL_SNAPSHOT_UNAVAILABLE"


def test_existing_canonical_identity_conflict_still_fails_closed():
    req = _Req()
    row = {
        "official_event_id": req.official_event_id,
        "event_start_time": req.event_start_time_utc,
        "event_status": "Scheduled",
        "home_team": req.home_team,
        "away_team": req.away_team,
        "venue_name": "PNC Park",
        "home_probable_pitcher": "Different Home Starter",
        "away_probable_pitcher": "Pirates Starter",
        "snapshot_id": "canonical-1",
        "snapshot_timestamp": (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat(),
        "feature_hydration_status": "PASS",
    }
    result = resolve_mlb_team_event_evidence(req, event_api=_Api([row]))
    assert result["ok"] is False
    assert result["code"] == "MLB_TEAM_EVENT_CALLER_EVIDENCE_CONTRADICTS_CANONICAL"
    assert "venue" in result["identity_mismatches"]
    assert "home_starting_pitcher" in result["identity_mismatches"]


def test_canonical_snapshot_remains_primary_when_consistent():
    req = _Req()
    row = {
        "official_event_id": req.official_event_id,
        "event_start_time": req.event_start_time_utc,
        "event_status": "Scheduled",
        "home_team": req.home_team,
        "away_team": req.away_team,
        "venue_name": "Wrigley Field",
        "home_probable_pitcher": "Cubs Starter",
        "away_probable_pitcher": "Pirates Starter",
        "snapshot_id": "canonical-2",
        "snapshot_timestamp": (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat(),
        "feature_hydration_status": "PASS",
    }
    result = resolve_mlb_team_event_evidence(req, event_api=_Api([row]))
    assert result["ok"] is True
    assert result["code"] == "MLB_TEAM_EVENT_CANONICAL_EVIDENCE_READY"
    assert result["canonical_source_snapshot_id"] == "canonical-2"
    assert result["evidence_authority"] == "CANONICAL_MLB_LEDGER"
    assert result["can_execute"] is False
