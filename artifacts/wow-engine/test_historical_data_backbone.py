from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from historical_data_backbone import (
    CanonicalIdentity,
    EvidenceDomain,
    HistoricalDataContractError,
    PointInTimeFeature,
    RawSourceSnapshot,
    SourceRightsState,
    build_sporting_feature_matrix,
    chronological_train_calibrate_test,
    load_source_manifest,
    payload_sha256,
)


UTC = timezone.utc


def _identity() -> CanonicalIdentity:
    return CanonicalIdentity(
        sport="WNBA",
        event_id="event-1",
        participant_id="player-1",
        team_id="team-a",
        opponent_id="team-b",
        provider_ids={"SPORTRADAR_WNBA": "provider-player-1"},
    )


def _feature(
    *,
    event_start: datetime | None = None,
    feature_as_of: datetime | None = None,
    retrieved_at: datetime | None = None,
    domain: EvidenceDomain = EvidenceDomain.SPORTING,
    name: str = "minutes_l5",
) -> PointInTimeFeature:
    event_start = event_start or datetime(2026, 7, 15, 23, 0, tzinfo=UTC)
    feature_as_of = feature_as_of or event_start - timedelta(hours=2)
    return PointInTimeFeature(
        identity=_identity(),
        event_start_time=event_start,
        feature_as_of=feature_as_of,
        feature_name=name,
        value=31.5,
        source_provider="SPORTRADAR_WNBA",
        source_payload_hash="a" * 64,
        evidence_domain=domain,
        retrieved_at=retrieved_at,
    )


def test_payload_hash_is_stable_across_mapping_key_order() -> None:
    left = {"player": "p1", "stats": {"pts": 20, "ast": 5}}
    right = {"stats": {"ast": 5, "pts": 20}, "player": "p1"}
    assert payload_sha256(left) == payload_sha256(right)


def test_payload_hash_changes_when_payload_changes() -> None:
    left = {"player": "p1", "pts": 20}
    right = {"player": "p1", "pts": 21}
    assert payload_sha256(left) != payload_sha256(right)


def test_raw_snapshot_allows_post_event_historical_retrieval() -> None:
    retrieved_at = datetime(2026, 9, 5, 3, 0, tzinfo=UTC)
    snapshot = RawSourceSnapshot.from_payload(
        sport="WNBA",
        provider="SPORTRADAR_WNBA",
        source_record_id="game-2024-07-15",
        retrieved_at=retrieved_at,
        payload={"settled": True, "pts": 20},
    )
    assert snapshot.retrieved_at == retrieved_at
    assert snapshot.can_execute is False


def test_feature_rejects_naive_event_timestamp() -> None:
    naive_start = datetime(2026, 7, 15, 23, 0)
    with pytest.raises(HistoricalDataContractError) as exc:
        _feature(
            event_start=naive_start,
            feature_as_of=datetime(2026, 7, 15, 20, 0, tzinfo=UTC),
        )
    assert exc.value.code == "HISTORICAL_TIMESTAMP_NAIVE"


def test_feature_rejects_exact_kickoff_timestamp() -> None:
    event_start = datetime(2026, 9, 5, 16, 0, tzinfo=UTC)
    with pytest.raises(HistoricalDataContractError) as exc:
        _feature(event_start=event_start, feature_as_of=event_start)
    assert exc.value.code == "HISTORICAL_FEATURE_LEAKAGE"


def test_feature_rejects_post_event_leakage() -> None:
    event_start = datetime(2026, 9, 5, 16, 0, tzinfo=UTC)
    with pytest.raises(HistoricalDataContractError) as exc:
        _feature(
            event_start=event_start,
            feature_as_of=event_start + timedelta(seconds=1),
        )
    assert exc.value.code == "HISTORICAL_FEATURE_LEAKAGE"


def test_historical_backfill_can_be_retrieved_later_if_feature_as_of_is_pregame() -> None:
    event_start = datetime(2024, 7, 15, 23, 0, tzinfo=UTC)
    record = _feature(
        event_start=event_start,
        feature_as_of=event_start - timedelta(hours=3),
        retrieved_at=datetime(2026, 9, 5, 3, 0, tzinfo=UTC),
    )
    assert record.feature_as_of < record.event_start_time
    assert record.retrieved_at > record.event_start_time


def test_market_evidence_is_rejected_from_sporting_feature_matrix() -> None:
    market = _feature(domain=EvidenceDomain.MARKET, name="sportsbook_implied_probability")
    with pytest.raises(HistoricalDataContractError) as exc:
        build_sporting_feature_matrix([market])
    assert exc.value.code == "MARKET_EVIDENCE_NOT_ALLOWED_IN_SPORTING_MODEL"


def test_sporting_evidence_enters_sporting_feature_matrix() -> None:
    sporting = _feature()
    matrix = build_sporting_feature_matrix([sporting])
    assert len(matrix) == 1
    assert matrix[0]["feature_name"] == "minutes_l5"
    assert "sportsbook_implied_probability" not in matrix[0]


def test_missing_canonical_identity_fails_closed() -> None:
    with pytest.raises(HistoricalDataContractError) as exc:
        CanonicalIdentity(
            sport="NFL",
            event_id="",
            participant_id="player-1",
            team_id="team-a",
            opponent_id="team-b",
        )
    assert exc.value.code == "HISTORICAL_IDENTITY_UNRESOLVED"


@dataclass(frozen=True)
class _Row:
    row_id: str
    event_start_time: datetime


def test_chronological_split_has_strict_non_overlapping_boundaries() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [_Row(str(index), base + timedelta(days=index)) for index in range(10)]
    split = chronological_train_calibrate_test(rows)

    assert max(row.event_start_time for row in split.train) < min(
        row.event_start_time for row in split.calibrate
    )
    assert max(row.event_start_time for row in split.calibrate) < min(
        row.event_start_time for row in split.test
    )


def test_chronological_split_never_separates_equal_time_group() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    rows = []
    for day in range(6):
        timestamp = base + timedelta(days=day)
        rows.extend([_Row(f"{day}-a", timestamp), _Row(f"{day}-b", timestamp)])

    split = chronological_train_calibrate_test(rows)
    membership = {}
    for fold_name, fold_rows in (
        ("train", split.train),
        ("calibrate", split.calibrate),
        ("test", split.test),
    ):
        for row in fold_rows:
            membership.setdefault(row.event_start_time, set()).add(fold_name)
    assert all(len(folds) == 1 for folds in membership.values())


def test_source_manifest_enforces_rights_and_domain_boundaries() -> None:
    manifest_path = Path(__file__).with_name("historical_source_manifest_v1.json")
    entries = load_source_manifest(manifest_path)
    by_key = {(entry.sport, entry.provider): entry for entry in entries}

    assert by_key[("MLB", "MLB_STATS_API")].production_training_eligible is True
    assert by_key[("NCAAF", "CFBD")].rights_state is SourceRightsState.V17_APPROVED
    assert by_key[("WNBA", "SPORTRADAR_WNBA")].production_training_eligible is False
    assert by_key[("TENNIS", "SACKMANN_TENNIS")].rights_state is SourceRightsState.RESEARCH_ONLY
    assert by_key[("MULTISPORT", "SPORTSDATAIO_VAULT")].evidence_domain is EvidenceDomain.MARKET
    assert by_key[("MULTISPORT", "SPORTSDATAIO_VAULT")].production_training_eligible is False
    assert all(entry.grants_model_capability is False for entry in entries)
    assert all(entry.can_execute is False for entry in entries)


def test_contract_objects_cannot_be_created_with_can_execute_true() -> None:
    with pytest.raises(TypeError):
        RawSourceSnapshot(
            sport="NFL",
            provider="NFLVERSE",
            source_record_id="row-1",
            retrieved_at=datetime(2026, 9, 5, tzinfo=UTC),
            payload_hash="b" * 64,
            can_execute=True,  # type: ignore[call-arg]
        )
