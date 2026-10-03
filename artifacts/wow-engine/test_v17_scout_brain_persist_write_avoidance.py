from pathlib import Path


EDGE_FUNCTION = (
    Path(__file__).resolve().parent
    / "v17"
    / "supabase"
    / "functions"
    / "wow-v17-scout-brain-persist"
    / "index.ts"
)


def test_source_snapshot_replay_avoids_unchanged_conflict_updates():
    text = EDGE_FUNCTION.read_text(encoding="utf-8")

    marker = "insert into wow_scout.source_snapshots"
    block = text.split(marker, 1)[1].split("counts.sourceSnapshots", 1)[0]

    assert "on conflict (snapshot_id) do update set" in block
    assert (
        "where wow_scout.source_snapshots.observed_at is distinct from excluded.observed_at"
        in block
    )
    assert (
        "wow_scout.source_snapshots.payload is distinct from excluded.payload"
        in block
    )
    assert (
        "wow_scout.source_snapshots.payload_hash is distinct from excluded.payload_hash"
        in block
    )
    assert (
        "wow_scout.source_snapshots.prediction_authority is distinct from false"
        in block
    )
    assert "wow_scout.source_snapshots.can_execute is distinct from false" in block


def test_candidate_source_link_replay_avoids_timestamp_only_rewrites():
    text = EDGE_FUNCTION.read_text(encoding="utf-8")

    marker = "insert into wow_scout.candidate_source_links"
    block = text.split(marker, 1)[1].split("counts.candidateSourceLinks", 1)[0]

    assert "on conflict (candidate_id,snapshot_id) do update set" in block
    assert "linked_at=now()" in block
    assert (
        "where wow_scout.candidate_source_links.link_status is distinct from excluded.link_status"
        in block
    )
    assert (
        "wow_scout.candidate_source_links.link_reason is distinct from excluded.link_reason"
        in block
    )
    assert (
        "wow_scout.candidate_source_links.provider_entities is distinct from excluded.provider_entities"
        in block
    )
    assert (
        "wow_scout.candidate_source_links.prediction_authority is distinct from false"
        in block
    )
    assert "wow_scout.candidate_source_links.can_execute is distinct from false" in block


DIRECT_PERSISTENCE = Path(__file__).resolve().parent / "v17" / "scout_brain_persistence.py"


def test_candidate_metadata_is_written_only_on_first_evidence_slice():
    text = EDGE_FUNCTION.read_text(encoding="utf-8")

    marker = "Candidate metadata is slice-independent."
    block = text.split(marker, 1)[1].split("if (evidences.length)", 1)[0]

    assert "if (isFirstSlice(row)) {" in block
    assert "insert into wow_scout.candidates" in block
    assert "counts.changed += insertedRows[0]?.inserted ? 1 : 0;" in block


def test_direct_persistence_matches_edge_write_avoidance():
    text = DIRECT_PERSISTENCE.read_text(encoding="utf-8")

    assert (
        "where wow_scout.source_snapshots.observed_at is distinct from excluded.observed_at"
        in text
    )
    assert (
        "where wow_scout.candidate_source_links.link_status is distinct from excluded.link_status"
        in text
    )
    assert (
        "wow_scout.candidate_source_links.provider_entities is distinct from excluded.provider_entities"
        in text
    )
