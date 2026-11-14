"""Registry coverage test for newly minted Cricsheet source-acquisition holds."""
import ast
from pathlib import Path
import re


def _extract_typed_hold_codes(code: str) -> set[str]:
    """Parse every literal hold independently of quote style or line breaks."""
    used: set[str] = set()
    for node in ast.walk(ast.parse(code)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "SourceCandidateHold":
            continue
        assert (len(node.args) == 1 and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)), "SOURCE_ARCHIVE_CODE_NON_LITERAL"
        value = node.args[0].value
        assert re.fullmatch(r"SOURCE_ARCHIVE_[A-Z0-9_]+", value), f"SOURCE_ARCHIVE_CODE_INVALID:{value}"
        used.add(value)
    return used

ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "artifacts" / "wow-engine" / "v17" / "cricsheet_source_candidate.py"
REGISTRY = ROOT / "artifacts" / "wow-engine" / "docs" / "failure_codes.md"


def test_every_new_cricsheet_typed_hold_is_registered_exactly_once():
    code = COLLECTOR.read_text(encoding="utf-8")
    ledger = REGISTRY.read_text(encoding="utf-8")
    used = _extract_typed_hold_codes(code)
    registered = []
    for line in ledger.splitlines():
        if line.startswith("| "):
            cells = [cell.strip().replace(chr(96), "") for cell in line.split("|")]
            if len(cells) >= 5 and cells[1].startswith("SOURCE_ARCHIVE_"):
                assert "SOURCE_ARCHIVE_INTEGRITY" in cells[2]
                assert cells[4] == "No"  # canonical registry has Rank eligible? column
                registered.append(cells[1])
    assert len(used) == 28
    assert len(registered) == len(set(registered))
    assert used == set(registered)


def test_archive_codes_are_never_equated_with_probability_or_auth_approval():
    ledger = REGISTRY.read_text(encoding="utf-8")
    assert "WOW V17 Canonical Failure-Code Registry" in ledger
    assert "can_execute=false" in ledger
    assert "V17_TERMINAL_REDUCER" in ledger
    assert "MODEL_INPUTS_INSUFFICIENT" in ledger
    assert "MODEL_SCORER_FAILED" in ledger


def test_code_extractor_not_fooled_by_quotes_or_multiline_calls():
    source = """raise SourceCandidateHold(\"SOURCE_ARCHIVE_EXISTING\")
raise SourceCandidateHold('SOURCE_ARCHIVE_NEW_SINGLE')
raise SourceCandidateHold(
    \"SOURCE_ARCHIVE_NEW_MULTILINE\"
)
"""
    assert _extract_typed_hold_codes(source) == {
        "SOURCE_ARCHIVE_EXISTING", "SOURCE_ARCHIVE_NEW_SINGLE", "SOURCE_ARCHIVE_NEW_MULTILINE",
    }


def test_dynamic_code_values_are_rejected_not_silently_omitted():
    import pytest

    with pytest.raises(AssertionError, match="SOURCE_ARCHIVE_CODE_NON_LITERAL"):
        _extract_typed_hold_codes("raise SourceCandidateHold(prefix + suffix)")
