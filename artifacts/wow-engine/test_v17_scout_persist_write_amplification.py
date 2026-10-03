from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EDGE_FUNCTION = (
    ROOT
    / "artifacts"
    / "wow-engine"
    / "v17"
    / "supabase"
    / "functions"
    / "wow-v17-scout-brain-persist"
    / "index.ts"
)
RESUME_WORKFLOW = ROOT / ".github" / "workflows" / "wow-v17-scout-persist-resume.yml"


def test_candidate_source_link_replay_skips_unchanged_conflicts():
    text = EDGE_FUNCTION.read_text(encoding="utf-8")
    block = text.split(
        "insert into wow_scout.candidate_source_links", 1
    )[1].split("counts.candidateSourceLinks", 1)[0]

    assert "on conflict (candidate_id,snapshot_id) do update set" in block
    assert (
        "where wow_scout.candidate_source_links.link_status "
        "is distinct from excluded.link_status"
    ) in block
    assert (
        "wow_scout.candidate_source_links.link_reason "
        "is distinct from excluded.link_reason"
    ) in block
    assert (
        "wow_scout.candidate_source_links.provider_entities "
        "is distinct from excluded.provider_entities"
    ) in block
    assert (
        "wow_scout.candidate_source_links.prediction_authority "
        "is distinct from excluded.prediction_authority"
    ) in block
    assert (
        "wow_scout.candidate_source_links.can_execute "
        "is distinct from excluded.can_execute"
    ) in block


def test_resume_does_not_force_replay_for_unrelated_main_sha():
    text = RESUME_WORKFLOW.read_text(encoding="utf-8")

    # An unrelated main SHA may resolve to the newest recoverable Scout source,
    # but recovery must never turn that fallback into an unconditional replay.
    assert 'force_replay="true"' not in text
    assert text.count('force_replay="false"') >= 2
    assert "Missing receipts still enter the normal recovery" in text
