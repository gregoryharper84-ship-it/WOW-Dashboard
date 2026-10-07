from pathlib import Path

EDGE = (
    Path(__file__).resolve().parent
    / "v17"
    / "supabase"
    / "functions"
    / "wow-v17-scout-brain-persist"
    / "index.ts"
)


def source() -> str:
    return EDGE.read_text(encoding="utf-8")


def test_prior_dossier_read_phase_is_governed_and_read_only():
    text = source()

    assert '"READ_PREVIOUS"' in text
    assert '.github/workflows/wow-v17-nightly-multiscout.yml@${REF}' in text
    assert 'readPreviousDossiers' in text
    assert 'from wow_scout.candidate_history' in text
    assert 'research_run_id is distinct from ${runId}' in text
    assert 'prediction_authority: false' in text
    assert 'can_execute: false' in text


def test_prior_dossier_read_is_bounded_and_uses_stable_candidate_identity():
    text = source()

    assert 'if (requested.length > 500)' in text
    assert 'PRIOR_DOSSIER_REQUEST_TOO_LARGE' in text
    assert 'await stableCandidateId(row)' in text
    assert 'select distinct on (candidate_id)' in text
    assert 'order by candidate_id, recorded_at desc, history_id desc' in text


def test_prior_dossier_read_handles_legacy_string_history_snapshots():
    text = source()

    assert 'function snapshotObject(value: unknown)' in text
    assert 'JSON.parse(value)' in text
    assert 'const dossier = asObj(snapshot.scout_dossier)' in text
    assert '"PRIOR_DOSSIER_MISSING"' in text
