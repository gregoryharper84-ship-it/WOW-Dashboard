from __future__ import annotations

import copy

import pytest

from v17 import wnba_historical_batch as batch


class Close:
    def __init__(self, event_id):
        self.event_id = event_id

    def payload(self):
        return {
            "event_id": self.event_id,
            "evidence_class": "ESPN_HISTORICAL_CLOSE_PROXY",
            "live_card_receipt_eligible": False,
            "probability_publishable": False,
            "can_execute": False,
        }


def _fake_acquire(*, identities, test_event_ids, summary_loader):
    del identities, summary_loader
    return {i: Close(i) for i in test_event_ids}, {
        "blocker_counts": {},
    }


def test_batch_is_bounded_resumable_and_never_publishable(monkeypatch):
    monkeypatch.setattr(batch, "acquire_wnba_close_proxies", _fake_acquire)
    ids = [str(i) for i in range(31)]
    identities = {i: {"home_team_id": "10", "away_team_id": "20"} for i in ids}
    one = batch.advance_wnba_historical_batch(
        identities=identities, holdout_event_ids=ids, summary_loader=lambda _: {},
    )
    assert one["attempted_n"] == 25
    assert one["remaining_event_n"] == 6
    assert one["status"] == "BATCH_IN_PROGRESS"
    two = batch.advance_wnba_historical_batch(
        identities=identities, holdout_event_ids=ids, summary_loader=lambda _: {},
        checkpoint=one,
    )
    assert two["bound_event_n"] == 31
    assert two["remaining_event_n"] == 0
    assert two["status"] == "BATCH_COMPLETE_REVIEW_REQUIRED"
    for receipt in (one, two):
        assert receipt["probability_publishable"] is False
        assert receipt["rank_eligible"] is False
        assert receipt["automatic_certification"] is False
        assert receipt["can_execute"] is False
        assert receipt["live_card_receipt_eligible"] is False


def test_batch_detects_tampering_and_holdout_mutation(monkeypatch):
    monkeypatch.setattr(batch, "acquire_wnba_close_proxies", _fake_acquire)
    identities = {"A": {"home_team_id": "1", "away_team_id": "2"}}
    one = batch.advance_wnba_historical_batch(
        identities=identities, holdout_event_ids=["A"], summary_loader=lambda _: {},
    )
    broken = copy.deepcopy(one)
    broken["evidence_by_event"]["A"]["can_execute"] = True
    with pytest.raises(ValueError, match="INTEGRITY_MISMATCH"):
        batch.advance_wnba_historical_batch(
            identities=identities, holdout_event_ids=["A"],
            summary_loader=lambda _: {}, checkpoint=broken,
        )
    with pytest.raises(ValueError, match="SCOPE_MISMATCH"):
        batch.advance_wnba_historical_batch(
            identities={"A": {"home_team_id": "9"}},
            holdout_event_ids=["A"], summary_loader=lambda _: {}, checkpoint=one,
        )


@pytest.mark.parametrize("size", [0, -1, 26, True, 1.5])
def test_batch_rejects_unbounded_sizes(size):
    with pytest.raises(ValueError, match="OUT_OF_BOUNDS"):
        batch.advance_wnba_historical_batch(
            identities={"A": {}}, holdout_event_ids=["A"],
            summary_loader=lambda _: {}, batch_size=size,
        )


def test_batch_preserves_unbound_events_as_blockers(monkeypatch):
    def unavailable(*, identities, test_event_ids, summary_loader):
        return {}, {"blocker_counts": {"SPREAD_ESPN_SUMMARY_NOT_FINAL": len(test_event_ids)}, "blockers_by_event": {event_id: "SPREAD_ESPN_SUMMARY_NOT_FINAL" for event_id in test_event_ids}}

    monkeypatch.setattr(batch, "acquire_wnba_close_proxies", unavailable)
    result = batch.advance_wnba_historical_batch(
        identities={"A": {}}, holdout_event_ids=["A"], summary_loader=lambda _: {},
    )
    assert result["bound_event_n"] == 0
    assert result["blocked_event_n"] == 1
    assert result["blockers_by_event"]["A"] == "SPREAD_ESPN_SUMMARY_NOT_FINAL"
    assert result["probability_publishable"] is False


def test_checkpoint_atomic_roundtrip_and_integrity(monkeypatch, tmp_path):
    monkeypatch.setattr(batch, "acquire_wnba_close_proxies", _fake_acquire)
    original = batch.advance_wnba_historical_batch(
        identities={"A": {"home_team_id": "1", "away_team_id": "2"}},
        holdout_event_ids=["A"], summary_loader=lambda _: {},
    )
    path = tmp_path / "wnba.json"
    batch.write_checkpoint_atomic(path, original)
    assert batch.read_checkpoint(path) == original
    assert not list(tmp_path.glob(".wnba-*"))
    path.write_text(path.read_text().replace('"can_execute": false', '"can_execute": true'))
    with pytest.raises(ValueError, match="INTEGRITY_MISMATCH"):
        batch.read_checkpoint(path)


def test_checkpoint_write_rejects_tampered_content(monkeypatch, tmp_path):
    monkeypatch.setattr(batch, "acquire_wnba_close_proxies", _fake_acquire)
    original = batch.advance_wnba_historical_batch(
        identities={"A": {}}, holdout_event_ids=["A"], summary_loader=lambda _: {},
    )
    original["attempted_event_ids"].append("B")
    with pytest.raises(ValueError, match="INTEGRITY_MISMATCH"):
        batch.write_checkpoint_atomic(tmp_path / "wnba.json", original)
