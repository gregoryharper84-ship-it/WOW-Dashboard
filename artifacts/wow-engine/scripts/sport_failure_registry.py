"""Derive the per-sport failure and status registries from production source.

The NHL registry was written by hand. Doing the same for NFL, NBA, NCAAF,
NCAAB, tennis and soccer would mean transcribing roughly 700 code literals and
their emitters by hand, which drifts immediately. This module instead derives
the rows from the source that raises the codes, and rewrites only the marked
blocks in the two registry documents.

Classification is deliberate and conservative:

* a code that looks like a failure (invalid/missing/failed/unavailable/...)
  is a failure code; failure tokens match from a segment boundary, so
  `REQUIRED` does not match `PREREQUISITE`;
* otherwise a code whose name is a success disposition (persisted, complete,
  registered, ready, ...) is a status, and is never a failure;
* otherwise a code that is a model/version label, a table or metric name, or a
  bare stream name is not a failure code and is listed as a non-failure label;
* anything left over defaults to a failure code, so an unrecognised code is
  registered rather than quietly dropped.

Run with ``--check`` to verify the documents are current without writing.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAILURE_DOC = ROOT / "docs" / "failure_codes.md"
STATUS_DOC = ROOT / "docs" / "sport_status_registries.md"

SPORTS = ("NFL", "NBA", "NCAAF", "NCAAB", "MLB", "WNBA", "TENNIS", "SOCCER")
SPORT_LABEL = {
    "NFL": "NFL",
    "NBA": "NBA",
    "NCAAF": "NCAAF",
    "NCAAB": "NCAAB",
    "MLB": "MLB",
    "WNBA": "WNBA",
    "TENNIS": "Tennis",
    "SOCCER": "Soccer",
}

FAILURE_TOKENS = (
    "INVALID", "MISSING", "FAILED", "FAILURE", "NOT_", "NO_", "UNAVAILABLE",
    "CONFLICT", "EXCEED", "STALE", "REJECT", "MISMATCH", "DUPLICATE", "EMPTY",
    "UNRESOLVED", "FORBIDDEN", "BLOCKED", "INSUFFICIENT", "ERROR", "TIMEOUT",
    "UNAUTHORIZED", "UNSAFE", "AMBIGUOUS", "ABSENT", "INCOMPLETE", "UNVERIFIED",
    "MALFORMED", "CORRUPT", "BROKEN", "REFUSED", "TRUNCATED", "LEAKAGE",
    "VIOLATION", "DRIFT", "UNSUPPORTED", "OVERLAP", "PHANTOM", "ORPHAN",
    "OUT_OF", "BEYOND", "UNPROVEN", "PROHIBITED", "COLLISION", "TAMPERED",
    "EXHAUSTED", "IMBALANCE", "DEGENERATE", "NOT_PASSED", "UNCONFIRMED",
    "CONTRADICTION", "REQUIRED", "NONFINITE", "NONNUMERIC", "REUSED",
    "UNCALIBRATED", "AFTER_FEATURE", "AFTER_KICKOFF", "AFTER_CUTOFF",
    "IN_FUTURE", "TOO_LARGE", "NEGATIVE", "ROW_LIMIT", "UNRECOGNIZED",
    "CONTRADICTS", "EXCEPTION", "OUTSIDE", "BLOCKERS", "BELOW",
    "UNEXPECTED", "UNMAPPED",
)

STATUS_TOKENS = (
    "PERSISTED", "BUILT", "COMPLETE", "COMPLETED", "REGISTERED", "CONFIRMED",
    "RECONCILED", "READY", "ELIGIBLE", "SUPPORTED", "PASS", "RESEARCH_ONLY",
    "EVIDENCE_ONLY", "CERTIFIED", "ATTEMPTED",
)

# Model families, artifact/feature schema versions, table names, metric names
# and capture-stream names are string literals but are not governed codes.
VERSION_RE = re.compile(r"_V\d+(_|$)")
LABEL_TOKENS = (
    "_LOGIT", "_LOGREG", "_RIDGE", "_RESIDUAL", "_PRIOR", "_FEATURES",
    "_FEATURE_SET", "_TOTAL_POINTS_", "_CALIBRATION_",
)
EXCLUDED_EXACT = {
    "NFL_DFS_FANTASY_SCORE", "NFL_EVENT_PROBABILITY", "NFL_GSIS_GAMEBOOK",
    "NFL_OFFICIAL_GSIS_GAMEBOOK", "NFL_SETTLEMENT_SOURCE", "NFL_SUPPORTED",
    "NFL_TRUSTED", "NFL_FULL_GAME_HOME_AWAY", "NFL_FITTED_MODEL_PATH_PROVEN",
    "NFL_PICKEM_POOL_V1",
    "NBA_CAPTURE", "NBA_KICKOFF", "NBA_SCALAR", "NBA_CDN_LIVE_BOXSCORE",
    "NBA_STATS_PLAYERGAMELOG_AND_NBA_CDN_BOXSCORE",
    "NCAAF_WATCH", "NCAAF_TRUSTED", "NCAAF_TEST_ONLY", "NCAAF_SCALE_ELIGIBLE",
    "NCAAF_PRIMARY_CANDIDATE", "NCAAF_SETTLED_PRIOR_RESULTS_RECONSTRUCTION_V1",
    "NCAAF_PROP_CANONICAL_STAT_NORMALIZATION_AND_TRAINING_CORPUS",
    "MLB_1IP_EMPIRICAL_SPECIALIST",
    "WNBA_COMPOSITE", "WNBA_SCHEDULE_URL", "WNBA_ROTATION_PLAYER",
    "WNBA_TRAINING_INGEST_PRE_SOURCE", "WNBA_TRAINING_SOURCE_PRE_RESULT",
    "SOCCER_1X2_MULTINOMIAL_LOGIT_V1", "SOCCER_1X2_MULTINOMIAL_LOGIT_V1_",
}
LABEL_PREFIXES = (
    "MLB_STATS_API_", "WNBA_STATS_", "WNBA_CDN_", "WNBA_LIVEDATA_",
    "WNBA_OFFICIAL_",
)
YEAR_RE = re.compile(r"(?:19|20)\d\d")


def _matches_token(code: str, token: str) -> bool:
    """Match a failure token at a segment boundary rather than mid-word.

    `REQUIRED` must not match `PREREQUISITE`, and `BELOW` must not match a
    word that merely contains it. Tokens that already carry a trailing
    underscore (`NOT_`, `NO_`) are matched as written: they anchor to the
    segment start they describe, and a compound segment such as
    `CANNOT_GRANT_...` is a refusal, not a capability.

    This stays conservative. A code that matches no token still defaults to
    `failure` in `classify`, and compound failure names such as
    `EXCEEDS_CERTIFIED_WINDOW` and `REJECTED` still match their token.
    """
    if token.endswith("_") or token.startswith("_"):
        return token in code
    return f"_{token}" in f"_{code}_"


def is_failure_name(code: str) -> bool:
    # `_HOLD` is a failure suffix, but `_HOLDOUT` is a cohort label.
    if re.search(r"_HOLD(?:_|$)", code):
        return True
    return any(_matches_token(code, token) for token in FAILURE_TOKENS)


def is_status_name(code: str) -> bool:
    if is_failure_name(code):
        return False
    return any(code.endswith(token) or f"_{token}_" in code for token in STATUS_TOKENS)


def is_label_name(code: str) -> bool:
    if is_failure_name(code) or is_status_name(code):
        return False
    if code in EXCLUDED_EXACT or code.startswith(LABEL_PREFIXES):
        return True
    if VERSION_RE.search(code):
        return True
    if YEAR_RE.search(code):
        return True
    return any(token in code for token in LABEL_TOKENS)


def classify(code: str) -> str:
    if is_failure_name(code):
        return "failure"
    if is_status_name(code):
        return "status"
    if is_label_name(code):
        return "label"
    return "failure"


def sport_modules(sport: str) -> list[Path]:
    prefix = sport.lower()
    modules = [
        *ROOT.glob(f"v17/{prefix}_*.py"),
        *ROOT.glob(f"{prefix}_*.py"),
    ]
    return sorted(
        path
        for path in modules
        if "tests" not in path.parts and "__pycache__" not in path.parts
    )


MESSAGE_RE = re.compile(r'"([A-Za-z][^"\n]{12,})"')


def _enclosing_function(lines: list[str], index: int) -> str:
    for back in range(index, -1, -1):
        stripped = lines[back].lstrip()
        match = re.match(r"(?:async )?def ([A-Za-z_][A-Za-z0-9_]*)\(", stripped)
        if match:
            return match.group(1)
    return "module scope"


def scan(sport: str) -> dict[str, dict]:
    """Return code -> {modules: {relpath: count}, message, function}."""
    found: dict[str, dict] = {}
    pattern = re.compile(rf'"({sport}_[A-Z0-9_]+)"')
    for module in sport_modules(sport):
        rel = module.relative_to(ROOT).as_posix()
        lines = module.read_text(encoding="utf-8", errors="ignore").splitlines()
        for index, line in enumerate(lines):
            for match in pattern.finditer(line):
                code = match.group(1)
                record = found.setdefault(
                    code,
                    {"modules": {}, "message": None, "function": _enclosing_function(lines, index)},
                )
                record["modules"][rel] = record["modules"].get(rel, 0) + 1
                if record["message"] is None:
                    probes = (line, lines[index + 1] if index + 1 < len(lines) else "")
                    for probe in probes:
                        candidate = MESSAGE_RE.search(probe)
                        if candidate and " " in candidate.group(1):
                            record["message"] = candidate.group(1)[:220]
                            break
    return found


def primary_module(modules: dict[str, int]) -> str:
    return sorted(modules.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _cell(text: str) -> str:
    return text.replace("|", "\\|").strip()


def failure_rows(sport: str, scanned: dict[str, dict], skip: set[str] | None = None) -> list[str]:
    rows = []
    skip = skip or set()
    for code in sorted(scanned):
        kind = classify(code)
        if kind != "failure" or code in skip:
            continue
        record = scanned[code]
        primary = primary_module(record["modules"])
        secondary = sorted(set(record["modules"]) - {primary})
        meaning = record["message"] or (
            f"No inline message; terminal code raised in `{record['function']}`."
        )
        if code.endswith("_"):
            display = f"`{code}*`"
        else:
            display = f"`{code}`"
        rows.append(
            "| {code} | {meaning} | `{primary}` | {secondary} | No |".format(
                code=display,
                meaning=_cell(meaning),
                primary=primary,
                secondary=", ".join(f"`{path}`" for path in secondary) or "—",
            )
        )
    return rows


def status_rows(sport: str, scanned: dict[str, dict]) -> list[str]:
    rows = []
    for code in sorted(scanned):
        if classify(code) != "status":
            continue
        record = scanned[code]
        primary = primary_module(record["modules"])
        meaning = record["message"] or (
            f"Informational disposition emitted by `{record['function']}`; grants no authority."
        )
        rows.append(
            "| `{code}` | `{function}` | `{primary}` | {meaning} | None |".format(
                code=code,
                function=record["function"],
                primary=primary,
                meaning=_cell(meaning),
            )
        )
    return rows


def label_rows(sport: str, scanned: dict[str, dict]) -> list[str]:
    rows = []
    for code in sorted(scanned):
        if classify(code) != "label":
            continue
        record = scanned[code]
        rows.append(
            "| `{code}` | `{primary}` |".format(code=code, primary=primary_module(record["modules"]))
        )
    return rows


def _block(sport: str, body: list[str]) -> str:
    label = SPORT_LABEL[sport]
    return "\n".join(
        [
            f"<!-- sport-failure-registry:{sport}:begin -->",
            f"## {label} failure-code registry (November 2026)",
            "",
            f"Derived from production modules by `scripts/sport_failure_registry.py`; run it",
            f"after changing {label} code. A code that is a model label, table or metric",
            "name, or a success disposition is catalogued in the tables below rather than",
            "as a failure. `can_execute=false` and dry-run-only remain invariant.",
            "",
            "| Code | Meaning | Emitting module | Also emitted by | Rank eligible? |",
            "|---|---|---|---|---|",
            *body,
            "",
            f"<!-- sport-failure-registry:{sport}:end -->",
        ]
    )


def status_block(sport: str, body: list[str], labels: list[str]) -> str:
    label = SPORT_LABEL[sport]
    return "\n".join(
        [
            f"<!-- sport-status-registry:{sport}:begin -->",
            f"## {label} success and informational dispositions (November 2026)",
            "",
            f"Emitted by the modules named below. Every disposition is informational only:",
            "none of them grants probability or publication authority, and none may be read",
            "as a certification.",
            "",
            "| Code | Emitting function | Emitting module | Meaning | Grants authority? |",
            "|---|---|---|---|---|",
            *body,
            "",
            f"### {label} literals that are not governed codes",
            "",
            "Model families, artifact/feature versions, table and metric names, and capture",
            "stream names. They are listed so the coverage check is exact rather than",
            "approximate; they are not dispositions and must not be treated as outcomes.",
            "",
            "| Literal | Emitting module |",
            "|---|---|",
            *labels,
            "",
            f"<!-- sport-status-registry:{sport}:end -->",
        ]
    )


def _replace_block(text: str, marker: str, replacement: str) -> str:
    begin = f"<!-- {marker}:begin -->"
    end = f"<!-- {marker}:end -->"
    if begin in text and end in text:
        head = text.split(begin, 1)[0]
        tail = text.split(end, 1)[1]
        return f"{head}{replacement}{tail}"
    return text.rstrip("\n") + "\n\n" + replacement + "\n"


def documented_outside_sport_tables(text: str) -> set[str]:
    stripped = re.sub(
        r"(?s)<!-- sport-failure-registry:[A-Z]+:begin -->.*?<!-- sport-failure-registry:[A-Z]+:end -->",
        "",
        text,
    )
    rows = re.findall(r"^\| `([A-Za-z0-9_:<>]+)` \|", stripped, re.M)
    return {row.split(":", 1)[0].split("<", 1)[0] for row in rows}


def render() -> tuple[str, str]:
    failure_text = FAILURE_DOC.read_text(encoding="utf-8")
    already_documented = documented_outside_sport_tables(failure_text)
    status_text = STATUS_DOC.read_text(encoding="utf-8") if STATUS_DOC.exists() else (
        "# WOW V17 per-sport status registries\n\n"
        "Companion to `docs/failure_codes.md`. Derived by\n"
        "`scripts/sport_failure_registry.py`.\n"
    )
    for sport in SPORTS:
        scanned = scan(sport)
        failure_text = _replace_block(
            failure_text,
            f"sport-failure-registry:{sport}",
            _block(sport, failure_rows(sport, scanned, already_documented)),
        )
        status_text = _replace_block(
            status_text,
            f"sport-status-registry:{sport}",
            status_block(sport, status_rows(sport, scanned), label_rows(sport, scanned)),
        )
    status_text = status_text.rstrip("\n") + "\n"
    return failure_text, status_text


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the documents are stale")
    args = parser.parse_args(argv)

    failure_text, status_text = render()
    current_failure = FAILURE_DOC.read_text(encoding="utf-8")
    current_status = STATUS_DOC.read_text(encoding="utf-8") if STATUS_DOC.exists() else ""

    if args.check:
        stale = []
        if failure_text != current_failure:
            stale.append(FAILURE_DOC.relative_to(ROOT).as_posix())
        if status_text != current_status:
            stale.append(STATUS_DOC.relative_to(ROOT).as_posix())
        if stale:
            print("stale registry documents: " + ", ".join(stale), file=sys.stderr)
            print("run `python scripts/sport_failure_registry.py`", file=sys.stderr)
            return 1
        print("registry documents are current")
        return 0

    FAILURE_DOC.write_text(failure_text, encoding="utf-8")
    STATUS_DOC.write_text(status_text, encoding="utf-8")
    for sport in SPORTS:
        scanned = scan(sport)
        kinds = {"failure": 0, "status": 0, "label": 0}
        for code in scanned:
            kinds[classify(code)] += 1
        print(
            f"{sport}: codes={len(scanned)} failure={kinds['failure']} "
            f"status={kinds['status']} label={kinds['label']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
