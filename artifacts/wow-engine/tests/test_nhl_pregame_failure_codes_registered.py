"""The new NHL pregame failure codes must be in the canonical registry."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "docs" / "failure_codes.md"
MODULES = (
    ROOT / "v17" / "nhl_starter_feed_adapters.py",
    ROOT / "v17" / "nhl_starter_goalie_confirmation.py",
    ROOT / "v17" / "nhl_pregame_orchestration.py",
)
# The gate module also carries pre-existing NHL evidence codes; only the
# confirmation-receipt codes introduced here are required in this assertion.
GATE_CODES = {
    "NHL_GOALIE_CONFIRMATION_RECEIPT_MISSING",
    "NHL_GOALIE_CONFIRMATION_RECEIPT_NOT_CONFIRMED",
    "NHL_GOALIE_CONFIRMATION_SOURCES_NOT_INDEPENDENT",
    "NHL_GOALIE_IDENTITY_SNAPSHOT_MISMATCH",
    "NHL_GOALIE_CONFIRMATION_TIME_MISMATCH",
    "NHL_GOALIE_IDENTITY_UNVERIFIED",
    "NHL_GOALIE_CONFIRMATION_AUTHORITY_INVALID",
}
PREFIXES = (
    "NHL_STARTER_FEED_",
    "NHL_GOALIE_",
    "NHL_STARTING_GOALIE_",
    "NHL_PREGAME_",
    "NHL_CANONICAL_IDENTITY_",
)
SUCCESS_CODES = {"NHL_PREGAME_GATE_INPUTS_COMPLETE"}


def _registry_bases(text: str) -> set[str]:
    rows = re.findall(r"^\| `([A-Za-z0-9_:<>]+)` \|", text, re.M)
    return {row.split(":", 1)[0] for row in rows}


def _emitted_codes() -> set[str]:
    text = "\n".join(path.read_text() for path in MODULES)
    codes = set(re.findall(r"NHL_[A-Z0-9_]+", text))
    codes = {
        code
        for code in codes
        if code.startswith(PREFIXES) or code == "NHL_EVENT_NOT_PREGAME"
    }
    return codes - SUCCESS_CODES


def test_every_emitted_starter_goalie_code_is_registered():
    emitted = _emitted_codes()
    assert "NHL_STARTER_FEED_GAMECENTER" not in emitted
    assert len(emitted) >= 30, sorted(emitted)
    registered = _registry_bases(REGISTRY.read_text())
    missing = sorted(code for code in emitted if code not in registered)
    assert missing == [], missing


def test_new_gate_codes_are_registered():
    registered = _registry_bases(REGISTRY.read_text())
    missing = sorted(code for code in GATE_CODES if code not in registered)
    assert missing == [], missing


def test_registry_section_declares_no_rank_or_model_unavailable():
    text = REGISTRY.read_text()
    section = text.split("## NHL pregame starter-goalie evidence pipeline", 1)[1]
    assert "MODEL_UNAVAILABLE" in section
    assert "`can_execute=false`" in section
    rows = re.findall(r"^\| `([A-Za-z0-9_:<>]+)` \| (.+) \| (Yes|No|Sporting[^|]*) \|$", section, re.M)
    assert rows, "expected registry rows"
    for code, _meaning, rank in rows:
        assert rank.strip() == "No", code


GATE_MODULE = ROOT / "v17" / "nhl_pregame_evidence_boundary.py"
NON_FAILURE_TOKENS = {
    "NHL_PUBLIC_WEB_API",
    "NHL_STARTER_GOALIE_INDEPENDENTLY_CONFIRMED",
}


def test_every_pregame_gate_module_code_is_registered():
    codes = set(re.findall(r"NHL_[A-Z0-9_]+", GATE_MODULE.read_text())) - NON_FAILURE_TOKENS
    assert len(codes) >= 15, sorted(codes)
    registered = _registry_bases(REGISTRY.read_text())
    missing = sorted(code for code in codes if code not in registered)
    assert missing == [], missing


GAMECENTER_MODULE = ROOT / "v17" / "nhl_pregame_gamecenter_capture.py"


def test_every_pregame_gamecenter_capture_code_is_registered():
    codes = set(re.findall(r"NHL_[A-Z0-9_]+", GAMECENTER_MODULE.read_text())) - NON_FAILURE_TOKENS
    assert len(codes) >= 12, sorted(codes)
    registered = _registry_bases(REGISTRY.read_text())
    missing = sorted(code for code in codes if code not in registered)
    assert missing == [], missing


CANDIDATE_MODULES = (
    ROOT / "v17" / "nhl_candidate_preflight.py",
    ROOT / "v17" / "nhl_model_maintenance.py",
)
CANDIDATE_NON_FAILURE_TOKENS = NON_FAILURE_TOKENS | {
    "NHL_CANDIDATE_PREFLIGHT_READY",
    "NHL_GOALIE_STATUS_UNCHANGED",
    "NHL_ACQUISITION_OR_TRAINING",
    "NHL_RECONCILIATION",
}


def test_every_candidate_preflight_and_maintenance_code_is_registered():
    codes: set[str] = set()
    for path in CANDIDATE_MODULES:
        codes |= set(re.findall(r"NHL_[A-Z0-9_]+", path.read_text()))
    codes -= CANDIDATE_NON_FAILURE_TOKENS
    assert len(codes) >= 40, sorted(codes)
    registered = _registry_bases(REGISTRY.read_text())
    missing = sorted(code for code in codes if code not in registered)
    assert missing == [], missing


IDENTITY_MODULE = ROOT / "v17" / "nhl_event_identity.py"
HTTP_STATUS_PREFIX = "NHL_OFFICIAL_SOURCE_HTTP_"


def test_every_event_identity_and_source_resolution_code_is_registered():
    codes = set(re.findall(r"NHL_[A-Z0-9_]+", IDENTITY_MODULE.read_text()))
    codes -= NON_FAILURE_TOKENS
    codes.discard(HTTP_STATUS_PREFIX)
    assert len(codes) >= 8, sorted(codes)
    registered = _registry_bases(REGISTRY.read_text())
    missing = sorted(code for code in codes if code not in registered)
    assert missing == [], missing
    assert any(base.startswith(HTTP_STATUS_PREFIX) for base in registered), HTTP_STATUS_PREFIX


TOTALS_MODULES = tuple(sorted((ROOT / "v17").glob("nhl_total_goals_*.py")))
TOTALS_HTTP_PREFIX = "NHL_TOTALS_COHORT_HTTP_"
TOTALS_NON_FAILURE_TOKENS = NON_FAILURE_TOKENS | {
    "NHL_TOTAL_GOALS_POISSON_RESEARCH_V1",
    "NHL_TOTALS_PRIOR_GAMES_V1",
    "NHL_FULL_GAME_REGIME_MIXTURE_RESEARCH_V1",
    "NHL_OFFICIAL_FULL_GAME_FINAL_SCORE_OT_SO_INCLUDED",
    "NHL_V1_SAME_COHORT_REPLAY",
    "NHL_OFFICIAL_PUBLIC_SETTLED_GAME_PROJECTIONS",
    "NHL_RETRIEVED_RETROSPECTIVE_FINAL_PROJECTION",
    "NHL_TOTALS_OFFICIAL_HISTORICAL_PROJECTION_VERIFIED",
    "NHL_TOTALS_REAL_SOURCE_RESEARCH_FEATURES_READY",
    "NHL_TOTALS_REGIME_FIT_INPUTS_RECONCILED_RESEARCH_ONLY",
    "NHL_TOTALS_RETROSPECTIVE_SOURCE_LABELS_RECONCILED",
    "NHL_TOTALS_REAL_5SEASON_REGIME_REPLAY_RESEARCH_ONLY",
    "NHL_TOTALS_OFFICIAL_REGIME_COHORT_RECONCILED",
    "NHL_TOTALS_OFFICIAL_REGIME_PARTIAL_RESEARCH_ONLY",
    "NHL_TOTALS_PARTIAL_RESEARCH_NO_FIT",
    "NHL_TOTALS_RESEARCH_MODEL_REPLAY_COMPLETED_NOT_CERTIFIED",
}


def test_every_total_goals_research_code_is_registered():
    codes: set[str] = set()
    for path in TOTALS_MODULES:
        codes |= set(re.findall(r"NHL_[A-Z0-9_]+", path.read_text()))
    codes -= TOTALS_NON_FAILURE_TOKENS
    codes.discard(TOTALS_HTTP_PREFIX)
    assert len(codes) >= 100, sorted(codes)
    registered = _registry_bases(REGISTRY.read_text())
    missing = sorted(code for code in codes if code not in registered)
    assert missing == [], missing
    assert any(base.startswith(TOTALS_HTTP_PREFIX) for base in registered)


GOALIE_SHOT_MODULES = tuple(sorted((ROOT / "v17").glob("nhl_goalie_shot_*.py")))
GOALIE_SHOT_HTTP_PREFIX = "NHL_BOX_HTTP_"
GOALIE_SHOT_NON_FAILURE_TOKENS = NON_FAILURE_TOKENS | {
    "NHL_GOALIE_SHOT_CHALLENGER_V1",
    "NHL_V1_SAME_COHORT_REPLAY",
    "NHL_REPLAY_BLOCKED",
}


def test_every_goalie_shot_research_code_is_registered():
    codes: set[str] = set()
    for path in GOALIE_SHOT_MODULES:
        codes |= set(re.findall(r"NHL_[A-Z0-9_]+", path.read_text()))
    codes -= GOALIE_SHOT_NON_FAILURE_TOKENS
    codes = {code for code in codes if not code.startswith(GOALIE_SHOT_HTTP_PREFIX)}
    assert len(codes) >= 17, sorted(codes)
    registered = _registry_bases(REGISTRY.read_text())
    missing = sorted(code for code in codes if code not in registered)
    assert missing == [], missing
    assert any(base.startswith(GOALIE_SHOT_HTTP_PREFIX) for base in registered)


OUTSIDE_V17_NHL_MODULES = tuple(
    path
    for path in sorted(ROOT.rglob("nhl_*.py"))
    if "v17" not in path.parts and "tests" not in path.parts
)
OUTSIDE_V17_HTTP_PREFIX = "NHL_SOURCE_HTTP_"
OUTSIDE_V17_NON_FAILURE_TOKENS = NON_FAILURE_TOKENS | {
    "NHL_REGULAR_SEASON_LOGISTIC_V1",
    "NHL_REGULAR_SEASON_FEATURES_V1",
    "NHL_PRIOR_SETTLED_RECONSTRUCTION_V1",
    "NHL_RS_LOGISTIC_V1",
}


def test_every_nhl_module_outside_v17_is_registered():
    assert OUTSIDE_V17_NHL_MODULES, "expected at least one NHL module outside v17"
    codes: set[str] = set()
    for path in OUTSIDE_V17_NHL_MODULES:
        codes |= set(re.findall(r"NHL_[A-Z0-9_]+", path.read_text()))
    codes -= OUTSIDE_V17_NON_FAILURE_TOKENS
    codes = {code for code in codes if not code.startswith(OUTSIDE_V17_HTTP_PREFIX)}
    assert len(codes) >= 16, sorted(codes)
    registered = _registry_bases(REGISTRY.read_text())
    missing = sorted(code for code in codes if code not in registered)
    assert missing == [], missing
    assert any(base.startswith(OUTSIDE_V17_HTTP_PREFIX) for base in registered)


def _tables(text: str):
    lines = text.splitlines()
    i = 0
    while i + 1 < len(lines):
        if lines[i].startswith("|") and re.match(r"^\|[-: |]+\|$", lines[i + 1].strip()):
            j = i + 2
            while j < len(lines) and lines[j].startswith("|"):
                j += 1
            yield lines[i], lines[i + 1], lines[i + 2:j]
            i = j
        else:
            i += 1


def test_every_nhl_failure_table_has_an_emitting_module_column():
    tables = 0
    for header, separator, body in _tables(REGISTRY.read_text()):
        if not any(row.startswith("| `NHL_") for row in body):
            continue
        tables += 1
        assert "| Emitting module |" in header, header
        assert "| Also emitted by |" in header, header
        assert len(header.split("|")) == len(separator.split("|")), header
    assert tables >= 19, tables


def _mentions(text: str, code: str) -> bool:
    """True only when `code` appears as a whole literal, not inside a longer code.

    A substring test lets a removed `NHL_FOO` hide behind `NHL_FOO_BAR`, so a
    renamed code could stay "present" in a module that no longer raises it.
    """
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(code)}(?![A-Za-z0-9_])", text) is not None


def test_every_nhl_failure_row_names_its_emitting_module():
    rows = 0
    for line in REGISTRY.read_text().splitlines():
        if not line.startswith("| `NHL_"):
            continue
        cells = [cell.strip() for cell in line.split("|")[1:-1]]
        assert len(cells) == 5, (line[:80], len(cells))
        code = cells[0].strip("`")
        emitter = cells[2].strip("`")
        assert emitter != "\u2014", code
        module = ROOT / emitter
        assert module.is_file(), (code, emitter)
        assert "tests" not in Path(emitter).parts, code
        needle = code.split(":", 1)[0].split("<", 1)[0]
        assert _mentions(module.read_text(encoding="utf-8"), needle), (code, emitter)
        rows += 1
    assert rows >= 270, rows


SHARED_EMITTERS = {
    "NHL_CANONICAL_IDENTITY_RECEIPT_MISSING": ["v17/nhl_pregame_evidence_boundary.py"],
    "NHL_CANONICAL_IDENTITY_RECEIPT_MISMATCH": ["v17/nhl_pregame_evidence_boundary.py"],
    "NHL_EVENT_NOT_PREGAME": ["v17/nhl_pregame_evidence_boundary.py"],
    "NHL_EVENT_TIME_INVALID": ["nhl_candidate_pipeline.py"],
    "NHL_TOTALS_SOURCE_ROWS_EMPTY": ["v17/nhl_total_goals_challenger.py"],
    "NHL_TOTALS_SOURCE_IDENTITY_CONFLICT": ["v17/nhl_total_goals_challenger.py"],
    "NHL_GAME_ID_DUPLICATE": ["v17/nhl_goalie_shot_runner.py"],
}


def test_shared_failure_codes_record_every_secondary_emitter():
    rows = {}
    for line in REGISTRY.read_text().splitlines():
        if not line.startswith("| `NHL_"):
            continue
        cells = [cell.strip() for cell in line.split("|")[1:-1]]
        assert len(cells) == 5, line[:80]
        rows[cells[0].strip("`")] = (cells[2].strip("`"), re.findall(r"`([^`]+\.py)`", cells[3]))
    documented = {code: secondary for code, (_primary, secondary) in rows.items() if secondary}
    assert set(documented) == set(SHARED_EMITTERS), (sorted(documented), sorted(SHARED_EMITTERS))
    for code, modules in SHARED_EMITTERS.items():
        primary, secondary = rows[code]
        assert secondary == modules, (code, secondary)
        needle = code.split(":", 1)[0].split("<", 1)[0]
        for module in modules:
            assert module != primary, (code, module)
            assert "tests" not in Path(module).parts, code
            assert _mentions((ROOT / module).read_text(encoding="utf-8"), needle), (code, module)


def test_no_documented_nhl_failure_code_is_stale():
    codes = {
        match.split(":", 1)[0].split("<", 1)[0]
        for match in re.findall(r"^\| `(NHL_[A-Za-z0-9_:<>]+)` \|", REGISTRY.read_text(), re.M)
    }
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in ROOT.rglob("*.py")
        if "tests" not in path.parts and not path.name.startswith("test_")
    )
    stale = sorted(code for code in codes if not _mentions(sources, code))
    assert stale == [], stale


def test_registry_guard_workflow_is_not_path_filtered():
    workflow = ROOT.parents[1] / ".github" / "workflows" / "wow-v17-nhl-failure-code-registry.yml"
    text = workflow.read_text(encoding="utf-8")
    assert "pull_request:" in text
    assert not re.search(r"(?m)^\s+paths:\s*$", text), (
        "the registry guard must run for any change that could emit an NHL code; "
        "a fixed path list cannot cover future emitting modules"
    )
