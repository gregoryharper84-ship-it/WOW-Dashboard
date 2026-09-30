from __future__ import annotations

from pathlib import Path


def _workflow_text() -> str:
    repo_root = Path(__file__).resolve().parents[3]
    return (repo_root / ".github" / "workflows" / "wow-v17-priority-prop-lifecycle.yml").read_text()


def test_wnba_priority_lifecycle_uses_backend_recovery_when_optional_bridge_is_invalid():
    text = _workflow_text()
    assert "WNBA_OIDC_OFFICIAL_SCHEDULE_VALIDATION_FAILED; falling back to backend official-source recovery" in text
    assert "WNBA_OIDC_BRIDGE_UNAVAILABLE_USING_BACKEND_OFFICIAL_SOURCE_RECOVERY" in text
    assert "if [ \"${wnba_bridge_ready}\" -eq 1 ]; then" in text
    assert 'body="$(printf \'{\"requested_date\":\"%s\",\"requested_timezone\":\"America/Chicago\",\"candidate_offset\":0,\"max_candidates\":%s}\'' in text
    assert 'post_oidc_json "${PRODUCER_PATH}" "${body}" "/tmp/${SPORT}-evidence-${slate_date}.json" 120' in text


def test_wnba_priority_lifecycle_keeps_governance_and_optional_bridge():
    text = _workflow_text()
    assert "WNBA_CDN_SCHEDULE_GITHUB_OIDC_BRIDGE" in text
    assert "official_schedule_provider" in text
    assert 'WOW_CAN_EXECUTE: "false"' in text
    assert 'WOW_DRY_RUN_ONLY: "true"' in text
    assert "probability_publishable" in text
    assert "automatic_certification" in text
    assert "automatic_promotion" in text
