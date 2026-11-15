"""Per-sport failure and status registries stay tied to the code that emits them.

The NHL lane keeps its hand-written registry and guards in
``test_nhl_pregame_failure_codes_registered`` / ``test_nhl_status_registry``.
The other sports are generated from source by
``scripts/sport_failure_registry.py``; these guards recompute the same scan and
fail when a code is emitted but unregistered, registered but no longer emitted,
attributed to a module that does not contain it, or silently reclassified.
"""
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAILURE_DOC = ROOT / "docs" / "failure_codes.md"
STATUS_DOC = ROOT / "docs" / "sport_status_registries.md"


def _generator():
    spec = importlib.util.spec_from_file_location(
        "sport_failure_registry", ROOT / "scripts" / "sport_failure_registry.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GEN = _generator()
SPORTS = GEN.SPORTS


def _scanned(sport):
    return GEN.scan(sport)


def _bucket(sport, kind):
    return {code for code in _scanned(sport) if GEN.classify(code) == kind}


def _block(text, marker):
    begin = f"<!-- {marker}:begin -->"
    end = f"<!-- {marker}:end -->"
    assert begin in text, marker
    assert end in text, marker
    return text.split(begin, 1)[1].split(end, 1)[0]


def _failure_rows(sport):
    block = _block(FAILURE_DOC.read_text(encoding="utf-8"), f"sport-failure-registry:{sport}")
    rows = {}
    for line in block.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.split("|")[1:-1]]
        assert len(cells) == 5, (sport, line[:90], len(cells))
        code = cells[0].strip("`").rstrip("*")
        rows[code] = {
            "meaning": cells[1],
            "emitter": cells[2].strip("`"),
            "secondary": re.findall(r"`([^`]+\.py)`", cells[3]),
            "rank": cells[4],
        }
    return rows


def _status_section(sport):
    block = _block(STATUS_DOC.read_text(encoding="utf-8"), f"sport-status-registry:{sport}")
    statuses, labels = {}, {}
    current = None
    for line in block.splitlines():
        if line.startswith("| Code |"):
            current = "status"
            continue
        if line.startswith("| Literal |"):
            current = "label"
            continue
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.split("|")[1:-1]]
        if current == "status":
            assert len(cells) == 5, (sport, line[:90])
            statuses[cells[0].strip("`")] = {
                "function": cells[1].strip("`"),
                "emitter": cells[2].strip("`"),
                "meaning": cells[3],
                "grants": cells[4],
            }
        elif current == "label":
            assert len(cells) == 2, (sport, line[:90])
            labels[cells[0].strip("`")] = cells[1].strip("`")
        else:
            raise AssertionError((sport, line[:90]))
    return statuses, labels


def _assert_emitter(sport, code, emitter):
    assert "tests" not in Path(emitter).parts, (sport, code, emitter)
    module = ROOT / emitter
    assert module.is_file(), (sport, code, emitter)
    needle = code.split(":", 1)[0].split("<", 1)[0].rstrip("*")
    assert needle in module.read_text(encoding="utf-8"), (sport, code, emitter)


def test_every_emitted_failure_code_is_registered():
    for sport in SPORTS:
        rows = _failure_rows(sport)
        scanned = _scanned(sport)
        documented = GEN.documented_outside_sport_tables(FAILURE_DOC.read_text(encoding="utf-8"))
        expected = {
            code for code in _bucket(sport, "failure") if code not in documented
        }
        assert set(rows) == expected, (sport, sorted(set(rows) ^ expected)[:10])


def test_no_registered_failure_code_is_stale():
    for sport in SPORTS:
        for code, row in _failure_rows(sport).items():
            assert GEN.classify(code.rstrip("*")) == "failure", (sport, code)
            _assert_emitter(sport, code, row["emitter"])
            assert row["rank"] == "No", (sport, code)
            assert row["meaning"], (sport, code)
            for secondary in row["secondary"]:
                assert secondary != row["emitter"], (sport, code)
                _assert_emitter(sport, code, secondary)


def test_failure_tables_do_not_duplicate_codes():
    seen = {}
    for sport in SPORTS:
        for code in _failure_rows(sport):
            assert code not in seen, (code, seen[code], sport)
            seen[code] = sport


def test_every_status_is_documented_and_non_authoritative():
    for sport in SPORTS:
        statuses, _labels = _status_section(sport)
        expected = _bucket(sport, "status")
        assert set(statuses) == expected, (sport, sorted(set(statuses) ^ expected))
        assert not (expected & _bucket(sport, "failure")), sport
        for code, row in statuses.items():
            assert row["grants"] == "None", (sport, code)
            assert row["meaning"], (sport, code)
            _assert_emitter(sport, code, row["emitter"])


def test_every_excluded_literal_is_listed_with_its_module():
    for sport in SPORTS:
        _statuses, labels = _status_section(sport)
        expected = _bucket(sport, "label")
        assert set(labels) == expected, (sport, sorted(set(labels) ^ expected))
        for literal, emitter in labels.items():
            _assert_emitter(sport, literal, emitter)


def test_every_scanned_code_is_classified_exactly_once():
    for sport in SPORTS:
        kinds = [_bucket(sport, kind) for kind in ("failure", "status", "label")]
        assert sum(len(bucket) for bucket in kinds) == len(_scanned(sport)), sport
        assert not (kinds[0] & kinds[1]) and not (kinds[0] & kinds[2]) and not (kinds[1] & kinds[2])


# Pinned independently of `_bucket`: these names carry failure tokens as
# trailing or embedded segments, so a classifier change that reclassified them
# as dispositions would otherwise update both the document and the expectation.
PINNED_FAILURE_CODES = (
    "NBA_POINTS_CANONICAL_EVIDENCE_REJECTED",
    "NCAAF_MODEL_REGISTRY_REJECTED",
    "NCAAB_PREGAME_MARKET_INPUT_REJECTED",
    "MLB_1IP_BF_HISTORY_EXCEEDS_CERTIFIED_WINDOW",
    "WNBA_PROP_FROZEN_SOURCE_CANNOT_GRANT_MODEL_CAPABILITY",
)


def test_pinned_failure_codes_classify_as_failure():
    emitted = {code for sport in SPORTS for code in _scanned(sport)}
    assert sorted(set(PINNED_FAILURE_CODES) - emitted) == [], "pinned code no longer emitted"
    reclassified = sorted(code for code in PINNED_FAILURE_CODES if GEN.classify(code) != "failure")
    assert reclassified == [], reclassified


def test_failure_token_matching_is_segment_bounded():
    assert GEN.is_failure_name("NFL_INPUTS_MISSING")
    assert GEN.is_failure_name("NFL_NOT_READY")
    assert GEN.is_failure_name("NFL_OUT_OF_WINDOW")
    # A compound failure name still matches its token at a segment boundary.
    assert GEN.is_failure_name("MLB_1IP_BF_HISTORY_EXCEEDS_CERTIFIED_WINDOW")
    assert GEN.is_failure_name("NCAAF_MODEL_REGISTRY_REJECTED")
    # `REQUIRED` inside `PREREQUISITE` is not the token; matching it would
    # register a safe name as a failure.
    assert not GEN.is_failure_name("NFL_PREREQUISITE_TABLE")
    assert GEN.classify("NBA_TOTALS_REGISTERED") == "status"


def test_no_status_or_label_carries_a_failure_token():
    def carries_failure_token(code):
        padded = f"_{code}_"
        return any(f"_{token.strip('_')}_" in padded for token in GEN.FAILURE_TOKENS)

    for sport in SPORTS:
        for kind in ("status", "label"):
            offenders = sorted(code for code in _bucket(sport, kind) if carries_failure_token(code))
            assert offenders == [], (sport, kind, offenders)


def test_registry_documents_match_the_generator():
    failure_text, status_text = GEN.render()
    assert failure_text == FAILURE_DOC.read_text(encoding="utf-8"), (
        "docs/failure_codes.md is stale; run python scripts/sport_failure_registry.py"
    )
    assert status_text == STATUS_DOC.read_text(encoding="utf-8"), (
        "docs/sport_status_registries.md is stale; run python scripts/sport_failure_registry.py"
    )


def test_registry_guard_workflow_runs_the_sport_guards():
    workflow = ROOT.parents[1] / ".github" / "workflows" / "wow-v17-nhl-failure-code-registry.yml"
    text = workflow.read_text(encoding="utf-8")
    assert "tests/test_sport_failure_registries.py" in text
    assert "sport_failure_registry.py --check" in text
