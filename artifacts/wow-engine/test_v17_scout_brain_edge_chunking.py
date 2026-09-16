import json

import pytest

from v17 import scout_brain_edge_sync as sync_mod


def handoff(team_rows=2, prop_rows=3):
    return {
        "can_execute": False,
        "status": "DISCOVERY_COMPLETE_WITH_SOURCE_BLOCKERS",
        "run_id": "wow-scout-1-1",
        "research_run_id": "wow-scout-1-1",
        "source_blockers": [{"scope": "sport", "reason_code": "ODDS_FETCH_FAILED"}],
        "model_handoff": {
            "team_event_candidates": [
                {
                    "can_execute": False,
                    "sport_key": "baseball_mlb",
                    "official_event_id": f"team-{i}",
                    "market_evidence": [{"bookmaker": "book", "market_key": "h2h", "price": -120}] * 20,
                }
                for i in range(team_rows)
            ],
            "prop_candidates": [
                {
                    "can_execute": False,
                    "sport_key": "baseball_mlb",
                    "official_event_id": f"prop-{i}",
                    "market_evidence": {"bookmaker": "book", "market_key": "batter_hits", "price": -110},
                }
                for i in range(prop_rows)
            ],
        },
    }


class Recorder:
    """Stands in for the Edge Function and records every phase it is sent."""

    def __init__(self, *, finalize_count=None):
        self.calls = []
        self.finalize_count = finalize_count

    def __call__(self, url, payload):
        self.calls.append(payload)
        phase = payload.get("persist_phase")
        if phase == "FINALIZE":
            uploaded = sum(len(c.get("candidates", [])) for c in self.calls if c.get("persist_phase") == "APPEND")
            return {
                "ok": True,
                "can_execute": False,
                "research_run_id": payload.get("research_run_id"),
                "candidate_count": self.finalize_count if self.finalize_count is not None else uploaded,
            }
        return {"ok": True, "can_execute": False, "research_run_id": payload.get("research_run_id")}

    def phases(self):
        return [c.get("persist_phase") for c in self.calls]

    def appended(self):
        return [row for c in self.calls if c.get("persist_phase") == "APPEND" for row in c["candidates"]]


@pytest.fixture
def recorder(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(sync_mod, "_post", rec)
    return rec


def test_upload_is_begin_then_appends_then_finalize(tmp_path, recorder, monkeypatch):
    monkeypatch.setenv("WOW_SCOUT_PERSIST_BATCH_SIZE", "2")
    src = tmp_path / "handoff.json"
    src.write_text(json.dumps(handoff()))
    receipt = tmp_path / "receipt.json"

    sync_mod.sync(str(src), str(receipt))

    phases = recorder.phases()
    assert phases[0] == "BEGIN"
    assert phases[-1] == "FINALIZE"
    assert phases.count("APPEND") >= 2
    assert set(phases) == {"BEGIN", "APPEND", "FINALIZE"}


def test_every_candidate_is_uploaded_exactly_once(tmp_path, recorder, monkeypatch):
    monkeypatch.setenv("WOW_SCOUT_PERSIST_BATCH_SIZE", "2")
    src = tmp_path / "handoff.json"
    src.write_text(json.dumps(handoff(team_rows=3, prop_rows=4)))

    sync_mod.sync(str(src), str(tmp_path / "receipt.json"))

    ids = [row["official_event_id"] for row in recorder.appended()]
    assert sorted(ids) == sorted([f"team-{i}" for i in range(3)] + [f"prop-{i}" for i in range(4)])
    assert len(ids) == len(set(ids))


def test_no_batch_carries_the_whole_slate(tmp_path, recorder, monkeypatch):
    monkeypatch.setenv("WOW_SCOUT_PERSIST_BATCH_SIZE", "2")
    src = tmp_path / "handoff.json"
    src.write_text(json.dumps(handoff(team_rows=4, prop_rows=4)))

    sync_mod.sync(str(src), str(tmp_path / "receipt.json"))

    for call in recorder.calls:
        assert "model_handoff" not in call
        assert len(call.get("candidates", [])) <= 2


def test_every_phase_carries_run_identity_and_governance(tmp_path, recorder):
    src = tmp_path / "handoff.json"
    src.write_text(json.dumps(handoff()))

    sync_mod.sync(str(src), str(tmp_path / "receipt.json"))

    for call in recorder.calls:
        assert call["can_execute"] is False
        assert call["research_run_id"] == "wow-scout-1-1"
        assert call["status"] == "DISCOVERY_COMPLETE_WITH_SOURCE_BLOCKERS"
        assert call["source_blockers"][0]["reason_code"] == "ODDS_FETCH_FAILED"


def test_receipt_records_what_was_uploaded(tmp_path, recorder, monkeypatch):
    monkeypatch.setenv("WOW_SCOUT_PERSIST_BATCH_SIZE", "2")
    src = tmp_path / "handoff.json"
    src.write_text(json.dumps(handoff(team_rows=3, prop_rows=4)))
    receipt_path = tmp_path / "receipt.json"

    receipt = sync_mod.sync(str(src), str(receipt_path))

    assert receipt["uploaded_candidate_rows"] == 7
    assert receipt["upload_batches"] >= 4
    assert json.loads(receipt_path.read_text())["uploaded_candidate_rows"] == 7


def test_receipt_disagreeing_with_the_upload_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(sync_mod, "_post", Recorder(finalize_count=1))
    src = tmp_path / "handoff.json"
    src.write_text(json.dumps(handoff(team_rows=2, prop_rows=2)))
    receipt_path = tmp_path / "receipt.json"

    with pytest.raises(RuntimeError, match="SCOUT_EDGE_PERSIST_RECEIPT_RECONCILIATION_FAILED"):
        sync_mod.sync(str(src), str(receipt_path))
    assert not receipt_path.exists()


def test_governance_invalid_handoff_is_never_uploaded(tmp_path, recorder):
    src = tmp_path / "handoff.json"
    src.write_text(json.dumps({**handoff(), "can_execute": True}))

    with pytest.raises(RuntimeError, match="SCOUT_HANDOFF_GOVERNANCE_INVALID"):
        sync_mod.sync(str(src), str(tmp_path / "receipt.json"))
    assert recorder.calls == []


def test_batches_are_bounded_by_serialized_size_not_just_count():
    heavy = [{"official_event_id": str(i), "market_evidence": [{"x": "y" * 200}] * 10} for i in range(6)]
    out = list(sync_mod.batches(heavy, max_candidates=100, max_bytes=4096))
    assert len(out) > 1
    assert sum(len(b) for b in out) == 6


def test_a_single_oversized_candidate_still_makes_progress():
    huge = [{"official_event_id": "big", "market_evidence": [{"x": "y" * 5000}]}]
    out = list(sync_mod.batches(huge, max_candidates=10, max_bytes=16))
    assert out == [huge[0:1]]


def test_empty_slate_still_opens_and_closes_the_run(tmp_path, recorder):
    src = tmp_path / "handoff.json"
    src.write_text(json.dumps(handoff(team_rows=0, prop_rows=0)))

    receipt = sync_mod.sync(str(src), str(tmp_path / "receipt.json"))

    assert recorder.phases() == ["BEGIN", "FINALIZE"]
    assert receipt["uploaded_candidate_rows"] == 0
