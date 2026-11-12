"""Registry coverage test for newly minted Cricsheet source-acquisition holds."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "artifacts" / "wow-engine" / "v17" / "cricsheet_source_candidate.py"
REGISTRY = ROOT / "docs" / "failure_codes.md"


def test_every_new_cricsheet_typed_hold_is_registered_exactly_once():
    code = COLLECTOR.read_text(encoding="utf-8")
    ledger = REGISTRY.read_text(encoding="utf-8")
    used = set(re.findall(r'SourceCandidateHold\("([A-Z][A-Z0-9_]+)"\)', code))
    registered = []
    for line in ledger.splitlines():
        if line.startswith("| "):
            cells = [cell.strip().replace(chr(96), "") for cell in line.split("|")]
            if len(cells) >= 5 and cells[1].startswith("SOURCE_ARCHIVE_"):
                assert cells[2] == "SOURCE_ARCHIVE_INTEGRITY"
                assert cells[4] == "BLOCKED"
                registered.append(cells[1])
    assert len(used) == 28
    assert len(registered) == len(set(registered))
    assert used == set(registered)


def test_archive_codes_are_never_equated_with_probability_or_auth_approval():
    ledger = REGISTRY.read_text(encoding="utf-8")
    assert "PARTIAL_REGISTRY_BACKFILL_REQUIRED" in ledger
    assert "can_execute=false" in ledger
    assert "V17_TERMINAL_REDUCER" in ledger
    assert "MODEL_INPUTS_INSUFFICIENT" in ledger
    assert "MODEL_SCORER_FAILED" in ledger
