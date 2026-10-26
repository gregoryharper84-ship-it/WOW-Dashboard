"""Negative acceptance for single-writer recovery (#823, #1806, #1309)."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import sys
import yaml
import pytest
from v17.engineering_dispatch_queue import standard_conflicts_with_rapid, standard_queue_excluding_rapid_conflicts
from v17.p0_parallel_dispatch import select_parallel, STREAM_ORDER

ROOT = Path(__file__).parents[2]
ENGINE = Path(__file__).parent
WORKFLOWS = (
    ".github/workflows/wow-v17-chatgpt-engineering-worker.yml",
    ".github/workflows/wow-v17-claude-engineering-worker.yml",
)

def row(id_, keys, *, p0=False, state="OPEN"):
    return dict(incident_id=str(id_), state=state, severity="P0" if p0 else "P1",
                execution_lane="RAPID" if p0 else "STANDARD",
                rapid_stream=("LLP_RESTORE" if str(id_) == "1388" else "B") if p0 else "",
                lease_group="P0_LLP_RESTORE" if p0 else "GLOBAL",
                priority_rank=(1 if str(id_) == "1388" else 5) if p0 else 7,
                conflict_keys=keys, can_execute=False,
                terminal_authority="V17_TERMINAL_REDUCER")

def queue(*records):
    return dict(records=list(records), can_execute=False, terminal_authority="V17_TERMINAL_REDUCER")

def test_open_p0_823_blocks_global_1309_but_keeps_unrelated_standard():
    q=queue(row(823,["ALL_SPORTS_MONEYLINE","LLP_USER_PATH"],p0=True),
            row(1309,["ALL_SPORTS_MONEYLINE","ALL_SPORTS_SPREADS"]),
            row(491,["PROP_FORWARD_EVIDENCE"]))
    assert standard_conflicts_with_rapid(q,"1309")== ("ALL_SPORTS_MONEYLINE",)
    result=standard_queue_excluding_rapid_conflicts(q)
    assert [x["incident_id"] for x in result["records"]]==["491"]
    assert result["deferred_standard"]==[{"incident_id":"1309","reason":"OPEN_P0_CONFLICT_KEYS","keys":["ALL_SPORTS_MONEYLINE"]}]
    assert result["can_execute"] is False

def test_close_or_nonoverlap_allows_standard_reentry():
    q=queue(row(823,["ALL_SPORTS_MONEYLINE"],p0=True,state="CLOSED"),row(1309,["ALL_SPORTS_MONEYLINE"]))
    assert standard_conflicts_with_rapid(q,"1309")==()
    # Live queue includes only OPEN issues; closed rows are outside the queue.
    live=queue(row(1309,["ALL_SPORTS_MONEYLINE"]))
    assert [x["incident_id"] for x in standard_queue_excluding_rapid_conflicts(live)["records"]]==["1309"]

def test_malformed_queue_and_identity_fail_closed():
    q=queue(row(823,["ALL_SPORTS_MONEYLINE"],p0=True),row(1309,["ALL_SPORTS_MONEYLINE"]))
    for bad in ({**q,"can_execute":True},{**q,"terminal_authority":"CHANGED"},
                {**q,"records":q["records"]+[row(1309,["OTHER"])]}):
        with pytest.raises(ValueError): standard_conflicts_with_rapid(bad,"1309")
    with pytest.raises(ValueError): standard_conflicts_with_rapid(q,"missing")
    with pytest.raises(ValueError): standard_conflicts_with_rapid(queue(row(823,[],p0=True),row(1309,["KEY"])),"1309")

def test_exact_standard_target_is_not_an_escape_hatch():
    for path in WORKFLOWS:
        text=(ROOT/path).read_text()
        assert "engineering_dispatch_queue.py check-standard" in text
        assert "STANDARD_TARGET_CONFLICTS_WITH_OPEN_RAPID_P0" in text
        assert "engineering_dispatch_queue.py standard" in text

def test_github_implementation_jobs_have_one_shared_concurrency_owner():
    groups=[]
    for path in WORKFLOWS:
        doc=yaml.safe_load((ROOT/path).read_text())
        assert doc["concurrency"]["cancel-in-progress"] is False
        groups.append(doc["concurrency"]["group"])
    assert groups==["wow-v17-engineering-implementation-single-writer"]*2

def test_rapid_selects_existing_high_priority_manifest_streams():
    assert "LLP_RESTORE" in STREAM_ORDER and "AUTONOMY_RESTORE" in STREAM_ORDER
    selection=select_parallel([row(1388,["LLP_USER_PATH"],p0=True),row(823,["LLP_USER_PATH"],p0=True)])
    # Both rows share one lease, and neither may become a second writer.
    assert selection["selected_count"]==1
    assert selection["selected"][0]["incident_id"]=="1388"

def test_cli_exact_standard_block_and_no_bypass(tmp_path):
    q=queue(row(823,["ALL_SPORTS_MONEYLINE"],p0=True),row(1309,["ALL_SPORTS_MONEYLINE"]))
    input_file=tmp_path/"queue.json"
    input_file.write_text(json.dumps(q))
    tool=ENGINE/"v17"/"engineering_dispatch_queue.py"
    cp=subprocess.run([sys.executable,str(tool),"check-standard","--manifest",str(ENGINE/"v17"/"engineering_dispatch_manifest.json"),"--queue",str(input_file),"--incident","1309"],capture_output=True,text=True)
    assert cp.returncode!=0 and "OPEN_P0_CONFLICT_KEYS" in cp.stderr
