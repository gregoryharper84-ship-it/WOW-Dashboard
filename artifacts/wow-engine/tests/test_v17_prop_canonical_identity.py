from v17.prop_canonical_identity import canonical_sporting_key, canonicalize_prop_manifest

def _row(snapshot_id, *, line=6.5, direction=None, captured="2099-09-20T10:00:00Z"):
    row = {"source_snapshot_id": snapshot_id, "captured_at": captured, "event_id": "evt-1", "event_start_time": "2099-09-20T20:00:00Z", "sport": "MLB", "player": "Pitcher One", "stat_type": "PITCHER_STRIKEOUTS", "line": line}
    if direction is not None: row["direction"] = direction
    return row

def test_snapshot_id_is_provenance_not_sporting_identity():
    rows = [_row(f"snap-{i}", captured=f"2099-09-20T1{i}:00:00Z") for i in range(4)]
    out = canonicalize_prop_manifest(rows)
    assert len(out) == 1
    assert out[0]["source_snapshot_ids"] == ["snap-0", "snap-1", "snap-2", "snap-3"]
    assert out[0]["source_snapshot_id"] == "snap-3"
    assert out[0]["source_instance_count"] == 4

def test_materially_different_lines_and_directions_remain_distinct():
    assert canonical_sporting_key(_row("a", line=6.5)) != canonical_sporting_key(_row("b", line=7.5))
    assert canonical_sporting_key(_row("a", direction="MORE")) != canonical_sporting_key(_row("b", direction="LESS"))
    assert len(canonicalize_prop_manifest([_row("a", line=6.5), _row("b", line=7.5)])) == 2
