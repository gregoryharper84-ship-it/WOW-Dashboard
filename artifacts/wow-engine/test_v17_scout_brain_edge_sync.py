import io
import json

from v17 import scout_brain_edge_sync as edge


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_edge_sync_uses_short_lived_oidc_and_writes_receipt(monkeypatch, tmp_path):
    input_path = tmp_path / "handoff.json"
    receipt_path = tmp_path / "receipt.json"
    input_path.write_text(json.dumps({
        "run_id": "wow-scout-test-1",
        "research_run_id": "wow-scout-test-1",
        "can_execute": False,
        "model_handoff": {"team_event_candidates": [], "prop_candidates": []},
    }))
    monkeypatch.setattr(edge, "mint_github_actions_oidc", lambda force=False: "short-lived-token")

    seen = {}

    def fake_urlopen(req, timeout=0):
        seen["auth"] = req.headers.get("Authorization")
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse({
            "ok": True,
            "research_run_id": "wow-scout-test-1",
            "candidate_count": 0,
            "changed_candidate_count": 0,
            "quarantined_count": 0,
            "sport_counts": {},
            "can_execute": False,
        })

    monkeypatch.setattr(edge, "urlopen", fake_urlopen)
    receipt = edge.sync(str(input_path), str(receipt_path))

    assert seen["auth"] == "Bearer short-lived-token"
    assert seen["body"]["can_execute"] is False
    assert receipt["can_execute"] is False
    assert json.loads(receipt_path.read_text())["research_run_id"] == "wow-scout-test-1"


def test_edge_sync_rejects_non_false_execution_authority(tmp_path):
    input_path = tmp_path / "handoff.json"
    input_path.write_text(json.dumps({"can_execute": True}))
    try:
        edge.sync(str(input_path), str(tmp_path / "receipt.json"))
    except RuntimeError as exc:
        assert str(exc) == "SCOUT_HANDOFF_GOVERNANCE_INVALID"
    else:
        raise AssertionError("expected fail-closed governance rejection")
