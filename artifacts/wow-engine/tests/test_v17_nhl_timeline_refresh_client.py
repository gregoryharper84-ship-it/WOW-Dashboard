"""NHL first-party timeline daily refresh: bounded, inert, fail-closed."""
from pathlib import Path
import importlib.util
import yaml

ROOT = Path(__file__).resolve().parents[3]
MOD = ROOT / "artifacts/wow-engine/v17/nhl_timeline_refresh_client.py"
WF = ROOT / ".github/workflows/wow-v17-nhl-model-maintenance.yml"


def _client():
    spec = importlib.util.spec_from_file_location("nhl_timeline_refresh_client", MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_declared_daily_protected_main_refresh_uses_existing_workflow():
    wf = yaml.safe_load(WF.read_text())
    content = WF.read_text()
    assert '15 11 * * *' in content
    job = wf["jobs"]["refresh-timeline"]
    assert "github.ref == 'refs/heads/main'" in str(job["if"])
    assert job["permissions"]["id-token"] == "write"
    assert job["permissions"]["contents"] == "read"
    assert job["needs"] == "verify"
    assert all(x.get("continue-on-error") is True for x in job["steps"])
    assert "GITHUB_STEP_SUMMARY" in content
    assert "nhl_timeline_refresh_client.py" in content
    assert "WOW_CAN_EXECUTE" in str(job["env"])


def test_valid_genuine_backend_receipt_is_only_source_refresh_not_certification():
    m = _client()
    good = {"status": "NHL_SOURCE_REINGEST_PERSISTED", "season": "20262027",
            "provenance_complete": True, "timeline_receipt_persisted": True,
            "automatic_certification": False, "automatic_promotion": False,
            "probability_publishable": False, "can_execute": False}
    result = m.classify_response(good)
    assert result["status"] == "SOURCE_TIMELINE_REFRESHED_REQUIRES_SCIENTIFIC_ACCEPTANCE"
    assert result["can_execute"] is False
    assert result["probability_publishable"] is False


def test_backend_hold_missing_receipt_fails_closed_without_success_claim():
    m = _client()
    outcome = m.classify_response({"status": "NHL_SOURCE_REINGEST_PERSISTED",
       "provenance_complete": True, "timeline_receipt_persisted": False,
       "automatic_certification": False, "automatic_promotion": False,
       "probability_publishable": False, "can_execute": False})
    assert outcome["status"] == "BLOCKED"
    assert outcome["code"] == "NHL_SOURCE_TIMELINE_RECEIPT_NOT_PERSISTED"


def test_reject_backend_exec_or_promotion_claims():
    m = _client()
    base = {"status": "NHL_SOURCE_REINGEST_PERSISTED",
       "provenance_complete": True, "timeline_receipt_persisted": True,
       "automatic_certification": False, "automatic_promotion": False,
       "probability_publishable": False, "can_execute": False}
    for key in ("can_execute", "automatic_certification", "automatic_promotion", "probability_publishable"):
        bad = dict(base, **{key: True})
        out = m.classify_response(bad)
        assert out["status"] == "BLOCKED"
        assert out["code"] == "NHL_SOURCE_REFRESH_CONTRACT_INVALID"


def test_typed_backend_failure_survives_no_write_fallback():
    m = _client()
    out = m.classify_response({"status": "BLOCKED", "code": "NHL_SOURCE_TIMELINE_INCOMPLETE",
                               "can_execute": False, "automatic_certification": False,
                               "automatic_promotion": False, "probability_publishable": False})
    assert out["status"] == "BLOCKED"
    assert out["code"] == "NHL_SOURCE_TIMELINE_INCOMPLETE"
