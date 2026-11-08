"""Regression: governed evidence repair must use the writer's canonical identity.

``pick_request_runtime_core._validate_evidence`` persists a stripped ``event_id``,
uppercase ``sport``, whitespace-collapsed ``player`` and the alias-resolved
canonical ``stat_type``. The primary ``wow_prop_evidence_snapshot`` RPC compares
event id/player exactly, so the fallback locator and its revalidation request
must use that same identity. Otherwise a valid governed snapshot is skipped and
reported as ``PROP_EVIDENCE_SNAPSHOT_NOT_FOUND``.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from pydantic import BaseModel

import pick_request_runtime_core as runtime_core
import prop_evidence_repair as evidence_repair
from prop_evidence_repair import _canonical_identity, _fallback_snapshot_ids, repair_prop_evidence


class _Query:
    def __init__(self, rows, filters):
        self._rows = rows
        self._filters = filters

    def select(self, *_args):
        return self

    def eq(self, key, value):
        self._filters.append((key, value))
        return self

    def lt(self, key, value):
        self._filters.append((key, value))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, _value):
        return self

    def execute(self):
        return SimpleNamespace(data=self._rows)


class _Client:
    def __init__(self, rows):
        self._rows = rows
        self.filters: list[tuple[str, object]] = []

    def table(self, name):
        assert name == "wow_prop_evidence_snapshots"
        return _Query(self._rows, self.filters)


class _Request(BaseModel):
    event_id: str
    event_start_time: str
    sport: str
    player: str
    stat_type: str
    line: float
    source_snapshot_id: str


def _req(**overrides):
    values = {
        "event_id": "espn-401909944",
        "sport": "NBA",
        "player": "LeBron James",
        "stat_type": "POINTS",
        "line": 25.5,
        "event_start_time": "2099-01-01T00:00:00+00:00",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _writer_row(
    *,
    event_id=" 2026020276 ",
    sport="nfl",
    player="  Puka   Nacua ",
    stat_type="pass-yards",
    line=84.5,
):
    """Build the exact snapshot the governed writer would persist for a row."""
    now = datetime.now(timezone.utc)
    captured = now - timedelta(minutes=2)
    event_start = now + timedelta(days=1)
    row = runtime_core.PickRequestRow(
        row_key="writer-contract",
        event_id=event_id,
        event_start_time=event_start.isoformat(),
        sport=sport,
        player=player,
        stat_type=stat_type,
        line=line,
        direction="MORE",
        evidence=runtime_core.RawPropEvidence.model_validate(
            {
                "captured_at": captured.isoformat(),
                "game_log": [80.0 + i for i in range(10)],
                "box_score_log": [{"game_id": f"game-{i}"} for i in range(10)],
                "role_status": {"status": "ACTIVE", "role": "STARTER"},
                "role_timestamp": captured.isoformat(),
                "opportunity_ledger": {"status": "PASS"},
                "source_timestamps": {"OFFICIAL_BOX_SCORE": captured.isoformat()},
                "evidence_version": "PROP_EVIDENCE_V1",
                "rate_provenance": "TEST_ONLY",
            }
        ),
    )
    canonical_stat = runtime_core._canonical_stat(sport, stat_type)
    normalized = runtime_core._validate_evidence(row, canonical_stat)
    _snapshot_id, _fingerprint, persisted = runtime_core._snapshot_payload(row, normalized)
    persisted["source_snapshot_id"] = _snapshot_id
    return persisted


def _governed_store(*snapshots):
    """Return the canonical governed snapshot row used by identity tests."""
    return {str(snapshot["source_snapshot_id"]): dict(snapshot) for snapshot in snapshots}


def _governed_fetch(store):
    """Mirror ``wow_prop_evidence_snapshot`` exactly enough to revalidate identity."""

    def fetch(req):
        row = store.get(str(req.source_snapshot_id))
        if row is None:
            return {
                "ok": False,
                "code": "PROP_EVIDENCE_SNAPSHOT_NOT_FOUND",
                "hydration_status": "FAILED",
                "probability_publishable": False,
                "can_execute": False,
            }
        if (
            str(row["event_id"]) != str(req.event_id)
            or str(row["sport"]).upper() != str(req.sport).upper()
            or str(row["player"]) != str(req.player)
            or str(row["stat_type"]).upper() != str(req.stat_type).upper()
            or float(row["line"]) != float(req.line)
        ):
            return {
                "ok": False,
                "code": "PROP_EVIDENCE_IDENTITY_MISMATCH",
                "hydration_status": "FAILED",
                "probability_publishable": False,
                "can_execute": False,
            }
        if (
            row["hydration_status"] != "PASS"
            or len(row["game_log"]) < 10
            or len(row["box_score_log"]) < 10
            or not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in row["game_log"])
            or not all(isinstance(value, dict) and value for value in row["box_score_log"])
        ):
            return {
                "ok": False,
                "code": "RUN_INVALID_ACQUISITION_INCOMPLETE",
                "hydration_status": row["hydration_status"],
                "game_log_count": len(row["game_log"]),
                "box_score_log_count": len(row["box_score_log"]),
                "probability_publishable": False,
                "can_execute": False,
            }
        return {
            "ok": True,
            "code": "PROP_EVIDENCE_READY",
            "probability_publishable": False,
            "can_execute": False,
            **row,
        }

    return fetch


def test_fallback_lookup_normalizes_sport_stat_and_player_identity():
    """Fallback lookup normalizes sport, stat alias and player whitespace."""
    client = _Client([{"source_snapshot_id": "snap-1", "captured_at": "2099-01-01T00:00:00+00:00"}])

    ids = _fallback_snapshot_ids(
        client,
        _req(sport="nba", player="  LeBron   James ", stat_type="points"),
    )

    assert ids == ["snap-1"]
    assert ("sport", "NBA") in client.filters
    assert ("player", "LeBron James") in client.filters
    assert ("stat_type", "POINTS") in client.filters
    assert ("event_id", "espn-401909944") in client.filters
    assert ("line", 25.5) in client.filters


def test_fallback_lookup_preserves_already_canonical_identity():
    """Canonical identity remains unchanged through fallback lookup."""
    client = _Client([])

    assert _fallback_snapshot_ids(client, _req(sport="NFL", stat_type="PASSING_YARDS")) == []

    assert ("sport", "NFL") in client.filters
    assert ("stat_type", "PASSING_YARDS") in client.filters


def test_fallback_lookup_applies_the_writer_stat_alias_map():
    """Fallback lookup resolves the writer's stat alias map."""
    client = _Client([])

    _fallback_snapshot_ids(client, _req(sport="nfl", stat_type="pass-yards"))

    assert ("sport", "NFL") in client.filters
    assert ("stat_type", "PASSING_YARDS") in client.filters


def test_fallback_lookup_matches_the_live_writer_alias_map_exactly():
    """Fallback normalization agrees with the live writer alias map."""
    client = _Client([])

    _fallback_snapshot_ids(client, _req(sport="nfl", stat_type="pass-yds"))

    # The runtime may extend the alias map after core import; the fallback must
    # follow the writer's live map, not a stale copy.
    assert ("stat_type", runtime_core._canonical_stat("NFL", "pass-yds")) in client.filters


def test_fallback_lookup_normalizes_hyphenated_stat_labels():
    """Hyphenated stat labels normalize consistently."""
    client = _Client([])

    _fallback_snapshot_ids(client, _req(sport="NFL", stat_type="receiving-yards"))

    assert ("sport", "NFL") in client.filters
    assert ("stat_type", "RECEIVING_YARDS") in client.filters


def test_fallback_lookup_trims_event_id_and_never_rewrites_line():
    """Fallback trims the event id while preserving the exact line."""
    client = _Client([])

    _fallback_snapshot_ids(client, _req(event_id=" 2026020276 ", line=6.5))

    assert ("event_id", "2026020276") in client.filters
    assert ("line", 6.5) in client.filters


def test_canonical_identity_matches_the_governed_writer_payload():
    """Canonical identity matches the writer payload field for field."""
    persisted = _writer_row()
    canonical = _canonical_identity(
        SimpleNamespace(
            event_id=" 2026020276 ",
            sport="nfl",
            player="  Puka   Nacua ",
            stat_type="pass-yards",
        )
    )

    assert canonical == (
        persisted["event_id"],
        persisted["sport"],
        persisted["player"],
        persisted["stat_type"],
    )
    assert canonical == ("2026020276", "NFL", "Puka Nacua", "PASSING_YARDS")


def test_repair_recovers_and_revalidates_the_canonical_governed_snapshot():
    """Repair recovers and revalidates the canonical governed snapshot."""
    persisted = _writer_row()
    store = _governed_store(persisted)
    requested_id = "11111111-1111-4111-8111-111111111111"
    req = _Request(
        event_id=" 2026020276 ",
        event_start_time=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        sport="nfl",
        player="  Puka   Nacua ",
        stat_type="pass-yards",
        line=84.5,
        source_snapshot_id=requested_id,
    )
    client = _Client([{"source_snapshot_id": persisted["source_snapshot_id"]}])

    result = repair_prop_evidence(req, primary_fetch=_governed_fetch(store), client=client)

    assert result["ok"] is True
    assert result["code"] == "PROP_EVIDENCE_READY"
    assert result["acquisition_repair_status"] == "RECOVERED_FROM_GOVERNED_SNAPSHOT"
    assert result["requested_source_snapshot_id"] == requested_id
    assert result["effective_source_snapshot_id"] == persisted["source_snapshot_id"]
    assert result["event_id"] == "2026020276"
    assert result["player"] == "Puka Nacua"
    assert result["sport"] == "NFL"
    assert result["stat_type"] == "PASSING_YARDS"
    assert result["line"] == 84.5
    assert result["captured_at"] < req.event_start_time
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
    assert [(attempt["path"], attempt["status"]) for attempt in result["acquisition_attempts"]] == [
        ("REQUESTED_SNAPSHOT", "FAILED"),
        ("GOVERNED_SNAPSHOT_FALLBACK", "PASS"),
    ]
    assert result["acquisition_attempts"][0]["code"] == "PROP_EVIDENCE_SNAPSHOT_NOT_FOUND"
    assert len(store) == 1, "repair must not fabricate or persist evidence"


def test_repair_does_not_accept_a_fallback_the_validator_rejects():
    """Rejected fallback evidence never becomes ready."""
    persisted = _writer_row()
    persisted["game_log"] = persisted["game_log"][:3]
    store = _governed_store(persisted)
    req = _Request(
        event_id=" 2026020276 ",
        event_start_time=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        sport="nfl",
        player="  Puka   Nacua ",
        stat_type="pass-yards",
        line=84.5,
        source_snapshot_id="11111111-1111-4111-8111-111111111111",
    )
    client = _Client([{"source_snapshot_id": persisted["source_snapshot_id"]}])

    result = repair_prop_evidence(req, primary_fetch=_governed_fetch(store), client=client)

    assert result["code"] == "PROP_EVIDENCE_SNAPSHOT_NOT_FOUND"
    assert result["acquisition_repair_status"] == "FALLBACKS_EXHAUSTED"
    assert result["effective_source_snapshot_id"] == req.source_snapshot_id
    assert result["acquisition_attempts"][-1]["status"] == "FAILED"
    assert result["acquisition_attempts"][-1]["code"] == "RUN_INVALID_ACQUISITION_INCOMPLETE"
    assert "game_log" not in result
    assert result["can_execute"] is False


def test_repair_preserves_the_original_blocker_when_the_lookup_fails():
    """Lookup failure preserves the original blocker."""
    class _FailingClient:
        def table(self, _name):
            raise RuntimeError("ledger unavailable")

    req = _Request(
        event_id="2026020276",
        event_start_time="2099-01-01T00:00:00+00:00",
        sport="NFL",
        player="Puka Nacua",
        stat_type="PASSING_YARDS",
        line=84.5,
        source_snapshot_id="11111111-1111-4111-8111-111111111111",
    )

    result = repair_prop_evidence(
        req,
        primary_fetch=lambda _req: {"ok": False, "code": "PROP_EVIDENCE_STALE"},
        client=_FailingClient(),
    )

    assert result["code"] == "PROP_EVIDENCE_STALE"
    assert result["acquisition_repair_status"] == "FALLBACK_LOOKUP_FAILED"
    assert result["effective_source_snapshot_id"] == req.source_snapshot_id
    assert result["acquisition_attempts"][-1]["code"] == "RuntimeError"


def test_repair_does_not_look_up_a_non_repairable_primary_failure():
    """Non-repairable primary failures skip fallback lookup."""
    looked_up = []

    class _Client:
        def table(self, name):
            looked_up.append(name)
            raise AssertionError("lookup must not run")

    req = _Request(
        event_id="2026020276",
        event_start_time="2099-01-01T00:00:00+00:00",
        sport="NFL",
        player="Puka Nacua",
        stat_type="PASSING_YARDS",
        line=84.5,
        source_snapshot_id="11111111-1111-4111-8111-111111111111",
    )

    result = repair_prop_evidence(
        req,
        primary_fetch=lambda _req: {"ok": False, "code": "PROP_EVIDENCE_IDENTITY_MISMATCH"},
        client=_Client(),
    )

    assert result["acquisition_repair_status"] == "NOT_REPAIRABLE"
    assert looked_up == []
    assert evidence_repair.REPAIRABLE_EVIDENCE_CODES
