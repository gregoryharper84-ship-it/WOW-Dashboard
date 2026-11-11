"""Public outcome log transport must fail closed before any database audit."""
from hashlib import sha256
import json
import pytest
from v17.ncaaf_cfbd_full_audit_log import (
    audited_lines, FullAuditLogHold, META_PREFIX, CHUNK_PREFIX
)


def bundle():
    rows=[
        {"event_id":"4012", "athlete_id":"42", "stat_type":"COMPLETIONS",
         "actual_value":12, "espn_team_id":"84"},
        {"event_id":"4012", "athlete_id":"42", "stat_type":"RUSHING_YARDS",
         "actual_value":-4, "espn_team_id":"84"},
    ]
    digest=sha256(json.dumps(rows,sort_keys=True,separators=(",", ":")).encode()).hexdigest()
    manifest={
        "status":"NCAAF_NCAA_ESPN_SOURCE_CROSSWALK_RESEARCH_ONLY",
        "season":2024,"can_execute":False,"probability_publishable":False,
        "model_training_authorized_by_crosswalk":False,
        "full_candidate_stat_cells":len(rows),
        "full_candidate_stat_rows_sha256":digest,
        "ncaa_source_sha256":"a"*64,"espn_source_sha256":"b"*64,
    }
    return manifest,rows


def test_public_source_transport_is_deterministic_and_has_no_private_data():
    m,rows=bundle()
    a=audited_lines(m,rows);b=audited_lines(m,rows)
    assert a==b and len(a)==2
    assert a[0].startswith(META_PREFIX)
    assert a[1].startswith(CHUNK_PREFIX)
    assert '"can_execute":false' in a[0]
    assert '"rows":2' in a[0]
    assert "player_name" not in "\n".join(a)
    assert "market_line" not in "\n".join(a)
    assert json.loads(a[1][len(CHUNK_PREFIX):])["rows"]==rows


def test_tampered_row_rejected_even_when_only_one_value_changes():
    m,rows=bundle()
    rows[0]["actual_value"]=15
    with pytest.raises(FullAuditLogHold,match="NCAAF_FULL_SOURCE_HASH_MISMATCH"):
        audited_lines(m,rows)


def test_invalid_integer_or_injection_rejected():
    m,rows=bundle()
    rows[0]["athlete_id"]="42' OR true"
    m["full_candidate_stat_rows_sha256"]=sha256(json.dumps(rows,sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    with pytest.raises(FullAuditLogHold,match="NCAAF_FULL_SOURCE_ROW_INVALID"):
        audited_lines(m,rows)


def test_duplicate_rows_rejected_after_hash_recomputed():
    m,rows=bundle()
    rows.append(dict(rows[0]))
    m["full_candidate_stat_cells"]=len(rows)
    m["full_candidate_stat_rows_sha256"]=sha256(json.dumps(rows,sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    with pytest.raises(FullAuditLogHold,match="NCAAF_FULL_SOURCE_DUPLICATE"):
        audited_lines(m,rows)


def test_missing_season_and_promotion_flag_rejected():
    m,rows=bundle()
    m["model_training_authorized_by_crosswalk"]=True
    with pytest.raises(FullAuditLogHold,match="NCAAF_FULL_SOURCE_MANIFEST_INVALID"):
        audited_lines(m,rows)


def test_unordered_candidate_rows_rejected():
    m,rows=bundle()
    rows.reverse()
    m["full_candidate_stat_rows_sha256"]=sha256(json.dumps(rows,sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    with pytest.raises(FullAuditLogHold,match="NCAAF_FULL_SOURCE_ORDER_INVALID"):
        audited_lines(m,rows)
