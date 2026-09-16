import json

import pytest

from v17 import scout_brain_edge_sync as sync_mod


def handoff(team_rows=2, prop_rows=3, evidence_rows=20):
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
                    "route": "LLP_TEAM_BETTING_ENGINE",
                    "market_evidence": [
                        {
                            "bookmaker": "book",
                            "market_key": "h2h",
                            "outcome_name": f"side-{j}",
                            "price": -120,
                            "payload": "x" * 500,
                        }
                        for j in range(evidence_rows)
                    ],
                    "market_evidence_historical": [{"x": "h" * 1000}] * 50,
                    "market_evidence_stale": [{"x": "s" * 1000}] * 50,
                }
                for i in range(team_rows)
            ],
            "prop_candidates": [
                {
                    "can_execute": False,
                    "sport_key": "baseball_mlb",
                    "official_event_id": f"prop-{i}",
                    "route": "WOW_PROP_LANE",
                    "market_evidence": {"bookmaker": "book", "market_key": "batter_hits", "price": -110},
                }
                for i in range(prop_rows)
            ],
        },
    }


class Recorder:
    """Stands in for the fragment-aware Edge Function."""

    def __init__(self, *, finalize_count=None, disagree_append=False):
        self.calls = []
        self.finalize_count = finalize_count
        self.disagree_append = disagree_append

    def __call__(self, url, payload):
        self.calls.append(payload)
        phase = payload.get("persist_phase")
        if phase == "APPEND":
            candidate_count = sum(
                1 for row in payload.get("candidates", []) if row.get("persistence_first_fragment") is True
            )
            return {
                "ok": True,
                "can_execute": False,
                "research_run_id": payload.get("research_run_id"),
                "batch_candidate_count": candidate_count + (1 if self.disagree_append else 0),
                "batch_fragment_count": len(payload.get("candidates", [])),
            }
        if phase == "FINALIZE":
            uploaded = sum(
                1
                for call in self.calls
                if call.get("persist_phase") == "APPEND"
                for row in call.get("candidates", [])
                if row.get("persistence_first_fragment") is True
            )
            return {
                "ok": True,
                "can_execute": False,
                "research_run_id": payload.get("research_run_id"),
                "candidate_count": self.finalize_count if self.finalize_count is not None else uploaded,
            }
        return {"ok": True, "can_execute": False, "research_run_id": payload.get("research_run_id")}

    def phases(self):
        return [c.get("persist_phase") for c in self.calls]

    def fragments(self):
        return [row for c in self.calls if c.get("persist_phase") == "APPEND" for row in c["candidates"]]


@pytest.fixture
def recorder(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(sync_mod, "_post", rec)
    return rec


def test_upload_is_begin_then_appends_then_finalize(tmp_path, recorder, monkeypatch):
    monkeypatch.setenv("WOW_SCOUT_PERSIST_BATCH_SIZE", "2")
    monkeypatch.setenv("WOW_SCOUT_PERSIST_FRAGMENT_BYTES", "4096")
    src = tmp_path / "handoff.json"
    src.write_text(json.dumps(handoff()))
    receipt = tmp_path / "receipt.json"

    sync_mod.sync(str(src), str(receipt))

    phases = recorder.phases()
    assert phases[0] == "BEGIN"
    assert phases[-1] == "FINALIZE"
    assert phases.count("APPEND") >= 2
    assert set(phases) == {"BEGIN", "APPEND", "FINALIZE"}


def test_every_original_candidate_is_counted_once_across_fragments(tmp_path, recorder, monkeypatch):
    monkeypatch.setenv("WOW_SCOUT_PERSIST_FRAGMENT_BYTES", "8192")
    src = tmp_path / "handoff.json"
    src.write_text(json.dumps(handoff(team_rows=3, prop_rows=4, evidence_rows=50)))

    receipt = sync_mod.sync(str(src), str(tmp_path / "receipt.json"))

    firsts = [row for row in recorder.fragments() if row["persistence_first_fragment"] is True]
    assert len(firsts) == 7
    assert len({row["persistence_candidate_id"] for row in firsts}) == 7
    assert receipt["uploaded_candidate_rows"] == 7
    assert receipt["uploaded_fragments"] > 7


def test_fragments_keep_stable_candidate_identity(tmp_path, recorder, monkeypatch):
    monkeypatch.setenv("WOW_SCOUT_PERSIST_FRAGMENT_BYTES", "8192")
    src = tmp_path / "handoff.json"
    src.write_text(json.dumps(handoff(team_rows=1, prop_rows=0, evidence_rows=80)))

    sync_mod.sync(str(src), str(tmp_path / "receipt.json"))

    fragments = recorder.fragments()
    assert len(fragments) > 1
    assert len({row["persistence_candidate_id"] for row in fragments}) == 1
    assert all(row["persistence_identity_evidence"] == fragments[0]["persistence_identity_evidence"] for row in fragments)
    assert sum(1 for row in fragments if row["persistence_first_fragment"]) == 1
    assert sum(1 for row in fragments if row["persistence_last_fragment"]) == 1


def test_heavy_historical_and_stale_arrays_are_digest_represented_not_retransmitted():
    row = handoff(team_rows=1, prop_rows=0)["model_handoff"]["team_event_candidates"][0]
    fragments = sync_mod.candidate_fragments(row, max_fragment_bytes=16 * 1024)

    assert fragments
    for fragment in fragments:
        assert "market_evidence_historical" not in fragment
        assert "market_evidence_stale" not in fragment
        omitted = fragment["persistence_omitted_evidence"]
        assert omitted["market_evidence_historical"]["count"] == 50
        assert omitted["market_evidence_stale"]["count"] == 50
        assert len(omitted["market_evidence_historical"]["sha256"]) == 64


def test_transport_header_excludes_bulk_registry_and_model_handoff():
    value = handoff()
    value["sport_scout_registry"] = {"blob": "x" * 100000}
    header = sync_mod.handoff_header(value)
    assert "model_handoff" not in header
    assert "sport_scout_registry" not in header
    assert header["research_run_id"] == "wow-scout-1-1"
    assert header["can_execute"] is False


def test_request_batches_are_bounded_after_fragmentation():
    rows = sync_mod.candidate_rows(handoff(team_rows=2, prop_rows=0, evidence_rows=100))
    fragments = sync_mod.fragmented_rows(rows, max_fragment_bytes=16 * 1024)
    out = list(sync_mod.batches(fragments, max_candidates=25, max_bytes=64 * 1024))

    assert len(out) > 1
    for batch in out:
        assert sum(sync_mod._fragment_size(row) for row in batch) <= 64 * 1024 or len(batch) == 1
        assert all(sync_mod._fragment_size(row) < 20 * 1024 for row in batch)


def test_every_phase_carries_run_identity_and_governance(tmp_path, recorder):
    src = tmp_path / "handoff.json"
    src.write_text(json.dumps(handoff()))

    sync_mod.sync(str(src), str(tmp_path / "receipt.json"))

    for call in recorder.calls:
        assert call["can_execute"] is False
        assert call["research_run_id"] == "wow-scout-1-1"
        assert call["status"] == "DISCOVERY_COMPLETE_WITH_SOURCE_BLOCKERS"
        assert call["source_blockers"][0]["reason_code"] == "ODDS_FETCH_FAILED"


def test_receipt_records_original_rows_and_transport_fragments(tmp_path, recorder, monkeypatch):
    monkeypatch.setenv("WOW_SCOUT_PERSIST_FRAGMENT_BYTES", "8192")
    src = tmp_path / "handoff.json"
    src.write_text(json.dumps(handoff(team_rows=3, prop_rows=4, evidence_rows=50)))
    receipt_path = tmp_path / "receipt.json"

    receipt = sync_mod.sync(str(src), str(receipt_path))

    assert receipt["uploaded_candidate_rows"] == 7
    assert receipt["uploaded_fragments"] > 7
    assert receipt["upload_batches"] >= 1
    assert json.loads(receipt_path.read_text())["uploaded_candidate_rows"] == 7


def test_append_candidate_count_disagreement_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(sync_mod, "_post", Recorder(disagree_append=True))
    src = tmp_path / "handoff.json"
    src.write_text(json.dumps(handoff(team_rows=1, prop_rows=0)))

    with pytest.raises(RuntimeError, match="SCOUT_EDGE_PERSIST_FRAGMENT_RECONCILIATION_FAILED"):
        sync_mod.sync(str(src), str(tmp_path / "receipt.json"))


def test_finalize_receipt_disagreeing_with_original_rows_fails_closed(tmp_path, monkeypatch):
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


def test_empty_slate_still_opens_and_closes_the_run(tmp_path, recorder):
    src = tmp_path / "handoff.json"
    src.write_text(json.dumps(handoff(team_rows=0, prop_rows=0)))

    receipt = sync_mod.sync(str(src), str(tmp_path / "receipt.json"))

    assert recorder.phases() == ["BEGIN", "FINALIZE"]
    assert receipt["uploaded_candidate_rows"] == 0
