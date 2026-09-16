"""Upload slicing for Scout brain persistence.

Sized against the real failing slate, run 35131443035: model-handoff.json was
51,208,176 bytes across 34 team/event candidates and no props, the first
candidate 1,135,529 bytes over 1,952 evidence rows and the largest 5,291,090
bytes over 2,704. Bounding whole candidates let that first candidate through
intact under both the 256 KiB and the 64 KiB bound, because a batch always
emitted at least one candidate.
"""
import json

import pytest

from v17 import scout_brain_edge_sync as sync_mod


def evidence_row(i):
    return {
        "bookmaker": f"book-{i % 17}",
        "bookmaker_title": f"Book {i % 17}",
        "market_key": "h2h" if i % 3 else "spreads",
        "outcome_name": f"Team {i % 2}",
        "price": -110 - (i % 40),
        "point": round(i % 9 * 0.5, 1),
        "link": f"https://example.invalid/{i}",
    }


def team_candidate(event="evt-1", rows=1952):
    return {
        "can_execute": False,
        "sport_key": "baseball_mlb",
        "official_event_id": event,
        "route": "LLP_TEAM_BETTING_ENGINE",
        "research_ceiling": "RESEARCH_INTEREST",
        "home_team": "Home",
        "away_team": "Away",
        "market_evidence": [evidence_row(i) for i in range(rows)],
    }


def prop_candidate(event="evt-9"):
    return {
        "can_execute": False,
        "sport_key": "baseball_mlb",
        "official_event_id": event,
        "route": "WOW_PROP_LANE",
        "market_evidence": {"bookmaker": "book", "market_key": "batter_hits", "price": -115},
    }


def handoff(team=2, props=2, rows=1952):
    return {
        "can_execute": False,
        "status": "DISCOVERY_COMPLETE_WITH_SOURCE_BLOCKERS",
        "run_id": "wow-scout-1-1",
        "research_run_id": "wow-scout-1-1",
        "source_blockers": [],
        "model_handoff": {
            "team_event_candidates": [team_candidate(f"evt-{i}", rows) for i in range(team)],
            "prop_candidates": [prop_candidate(f"prop-{i}") for i in range(props)],
        },
    }


class Recorder:
    def __init__(self, *, finalize_count=None):
        self.calls = []
        self.finalize_count = finalize_count

    def __call__(self, url, payload):
        self.calls.append(payload)
        if payload.get("persist_phase") == "FINALIZE":
            finals = sum(
                1
                for c in self.calls
                if c.get("persist_phase") == "APPEND"
                for e in c["candidates"]
                if e["evidence_slice"]["final"]
            )
            return {
                "ok": True, "can_execute": False,
                "research_run_id": payload.get("research_run_id"),
                "candidate_count": self.finalize_count if self.finalize_count is not None else finals,
            }
        return {"ok": True, "can_execute": False, "research_run_id": payload.get("research_run_id")}

    def appends(self):
        return [c for c in self.calls if c.get("persist_phase") == "APPEND"]


@pytest.fixture
def recorder(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(sync_mod, "_post", rec)
    return rec


def test_one_oversized_candidate_no_longer_travels_whole(tmp_path, recorder):
    src = tmp_path / "h.json"
    src.write_text(json.dumps(handoff(team=1, props=0, rows=1952)))

    sync_mod.sync(str(src), str(tmp_path / "r.json"))

    appends = recorder.appends()
    assert len(appends) > 1, "a 1,952-row candidate must not be one request"
    for call in appends:
        body = len(json.dumps(call, separators=(",", ":")).encode())
        assert body <= sync_mod.DEFAULT_REQUEST_BYTES, (
            f"serialized POST is {body} bytes, over the configured "
            f"{sync_mod.DEFAULT_REQUEST_BYTES} ceiling"
        )


def test_every_evidence_row_is_uploaded_exactly_once(tmp_path, recorder):
    src = tmp_path / "h.json"
    src.write_text(json.dumps(handoff(team=2, props=2, rows=400)))

    sync_mod.sync(str(src), str(tmp_path / "r.json"))

    seen = []
    for call in recorder.appends():
        for entry in call["candidates"]:
            for row in sync_mod.evidence_list(entry):
                seen.append((entry["official_event_id"], row.get("link"), row["market_key"], row["price"]))
    assert len(seen) == len(set(seen)), "an evidence row was uploaded twice"
    assert len(seen) == 400 * 2 + 2


def test_each_candidate_has_exactly_one_final_slice(tmp_path, recorder):
    src = tmp_path / "h.json"
    src.write_text(json.dumps(handoff(team=3, props=2, rows=300)))

    sync_mod.sync(str(src), str(tmp_path / "r.json"))

    finals = {}
    for call in recorder.appends():
        for entry in call["candidates"]:
            if entry["evidence_slice"]["final"]:
                finals[entry["official_event_id"]] = finals.get(entry["official_event_id"], 0) + 1
    assert sorted(finals) == ["evt-0", "evt-1", "evt-2", "prop-0", "prop-1"]
    assert set(finals.values()) == {1}, "a candidate must finalize once, so it is counted once"


def test_slice_indices_are_contiguous_and_offsets_align(tmp_path, recorder):
    src = tmp_path / "h.json"
    src.write_text(json.dumps(handoff(team=1, props=0, rows=500)))

    sync_mod.sync(str(src), str(tmp_path / "r.json"))

    slices = [e["evidence_slice"] for c in recorder.appends() for e in c["candidates"]]
    assert [s["index"] for s in slices] == list(range(len(slices)))
    offset = 0
    for s in slices:
        assert s["offset"] == offset
        assert s["total"] == 500
        offset += s["count"]
    assert offset == 500


def test_candidate_metadata_rides_with_every_slice(tmp_path, recorder):
    src = tmp_path / "h.json"
    src.write_text(json.dumps(handoff(team=1, props=0, rows=400)))

    sync_mod.sync(str(src), str(tmp_path / "r.json"))

    for call in recorder.appends():
        for entry in call["candidates"]:
            assert entry["sport_key"] == "baseball_mlb"
            assert entry["official_event_id"] == "evt-0"
            assert entry["route"] == "LLP_TEAM_BETTING_ENGINE"
            assert entry["can_execute"] is False
            assert entry["research_ceiling"] == "RESEARCH_INTEREST"


def test_prop_rows_are_not_sliced_and_keep_their_object_shape(tmp_path, recorder):
    src = tmp_path / "h.json"
    src.write_text(json.dumps(handoff(team=0, props=3, rows=0)))

    sync_mod.sync(str(src), str(tmp_path / "r.json"))

    entries = [e for c in recorder.appends() for e in c["candidates"]]
    assert len(entries) == 3
    for entry in entries:
        # The Edge Function's prop identity algorithm reads the first evidence
        # row, so the object shape must survive the wire unchanged.
        assert isinstance(entry["market_evidence"], dict)
        assert entry["evidence_slice"]["final"] is True


def test_a_candidate_with_no_evidence_still_finalizes(tmp_path, recorder):
    row = team_candidate("evt-empty", rows=0)
    row["market_evidence"] = []
    src = tmp_path / "h.json"
    src.write_text(json.dumps({
        "can_execute": False, "run_id": "r", "research_run_id": "r",
        "model_handoff": {"team_event_candidates": [row], "prop_candidates": []},
    }))

    sync_mod.sync(str(src), str(tmp_path / "r.json"))

    entries = [e for c in recorder.appends() for e in c["candidates"]]
    assert len(entries) == 1
    assert entries[0]["evidence_slice"] == {"index": 0, "final": True, "offset": 0, "count": 0, "total": 0}


def test_phase_order_is_begin_appends_finalize(tmp_path, recorder):
    src = tmp_path / "h.json"
    src.write_text(json.dumps(handoff(team=1, props=1, rows=200)))

    sync_mod.sync(str(src), str(tmp_path / "r.json"))

    phases = [c.get("persist_phase") for c in recorder.calls]
    assert phases[0] == "BEGIN"
    assert phases[-1] == "FINALIZE"
    assert set(phases[1:-1]) == {"APPEND"}


def test_receipt_records_candidates_evidence_and_requests(tmp_path, recorder):
    src = tmp_path / "h.json"
    src.write_text(json.dumps(handoff(team=2, props=1, rows=250)))
    receipt_path = tmp_path / "r.json"

    receipt = sync_mod.sync(str(src), str(receipt_path))

    assert receipt["uploaded_candidate_rows"] == 3
    assert receipt["uploaded_evidence_rows"] == 250 * 2 + 1
    assert receipt["upload_requests"] >= 2
    assert json.loads(receipt_path.read_text())["uploaded_evidence_rows"] == 501


def test_receipt_disagreeing_with_the_upload_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(sync_mod, "_post", Recorder(finalize_count=1))
    src = tmp_path / "h.json"
    src.write_text(json.dumps(handoff(team=2, props=1, rows=50)))
    receipt_path = tmp_path / "r.json"

    with pytest.raises(RuntimeError, match="RECEIPT_RECONCILIATION_FAILED"):
        sync_mod.sync(str(src), str(receipt_path))
    assert not receipt_path.exists()


def test_governance_invalid_handoff_is_never_uploaded(tmp_path, recorder):
    src = tmp_path / "h.json"
    src.write_text(json.dumps({**handoff(team=1, props=0, rows=10), "can_execute": True}))

    with pytest.raises(RuntimeError, match="SCOUT_HANDOFF_GOVERNANCE_INVALID"):
        sync_mod.sync(str(src), str(tmp_path / "r.json"))
    assert recorder.calls == []


def test_evidence_row_bound_is_respected_per_request(tmp_path, recorder, monkeypatch):
    monkeypatch.setenv("WOW_SCOUT_PERSIST_EVIDENCE_ROWS", "40")
    src = tmp_path / "h.json"
    src.write_text(json.dumps(handoff(team=2, props=0, rows=300)))

    sync_mod.sync(str(src), str(tmp_path / "r.json"))

    for call in recorder.appends():
        rows = sum(len(sync_mod.evidence_list(e)) for e in call["candidates"])
        assert rows <= 40, f"request carried {rows} evidence rows"


def test_serialized_post_respects_the_configured_ceiling(tmp_path, recorder, monkeypatch):
    """The advertised bound is on the POST, envelope included.

    The slicer budgets candidate metadata and evidence, so the handoff header,
    phase marker and JSON wrapper have to be paid for out of the same ceiling.
    Asserting on the real serialized request is the only way CI can prove the
    contract the workflow advertises.
    """
    monkeypatch.setenv("WOW_SCOUT_PERSIST_REQUEST_BYTES", "40000")
    monkeypatch.setenv("WOW_SCOUT_PERSIST_EVIDENCE_ROWS", "10000")
    src = tmp_path / "h.json"
    handoff_body = handoff(team=3, props=2, rows=600)
    # A bulky header is the case the old accounting silently ignored.
    handoff_body["source_blockers"] = [
        {"scope": "event", "event_id": f"e-{i}", "reason_code": "ODDS_FETCH_FAILED", "detail": "x" * 200}
        for i in range(20)
    ]
    src.write_text(json.dumps(handoff_body))

    sync_mod.sync(str(src), str(tmp_path / "r.json"))

    sizes = [len(json.dumps(c, separators=(",", ":")).encode()) for c in recorder.appends()]
    assert sizes, "no APPEND was sent"
    assert max(sizes) <= 40000, f"largest serialized POST was {max(sizes)} bytes, over the 40000 ceiling"


def test_envelope_is_charged_against_the_request_budget():
    header = {"can_execute": False, "run_id": "r", "research_run_id": "r",
              "source_blockers": [{"reason_code": "X", "detail": "y" * 5000}]}
    assert sync_mod.envelope_bytes(header) > 5000
    assert sync_mod.envelope_bytes({"can_execute": False}) < 200


def test_a_single_evidence_row_over_the_ceiling_still_makes_progress(tmp_path, recorder, monkeypatch):
    """The one case the ceiling cannot hold, bounded to exactly one row.

    Trimming stops at a single row, so an evidence row larger than the whole
    budget still travels rather than looping forever. Real rows are ~2 KB
    against a 256 KiB ceiling, so this is a guard, not an expected path.
    """
    monkeypatch.setenv("WOW_SCOUT_PERSIST_REQUEST_BYTES", "9000")
    row = team_candidate("evt-huge", rows=0)
    row["market_evidence"] = [{"bookmaker": "b", "market_key": "h2h", "price": -110, "blob": "z" * 30000}] * 2
    src = tmp_path / "h.json"
    src.write_text(json.dumps({
        "can_execute": False, "run_id": "r", "research_run_id": "r",
        "model_handoff": {"team_event_candidates": [row], "prop_candidates": []},
    }))

    sync_mod.sync(str(src), str(tmp_path / "r.json"))

    appends = recorder.appends()
    assert len(appends) == 2, "each oversized row must travel alone"
    for call in appends:
        assert sum(len(sync_mod.evidence_list(e)) for e in call["candidates"]) == 1
    finals = [e for c in appends for e in c["candidates"] if e["evidence_slice"]["final"]]
    assert len(finals) == 1
