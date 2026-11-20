"""Single source of truth for WOW protected-main check identities (redesign R3).

Check names and GitHub App IDs used to be repeated in about 20 places: bash
arrays, jq filters, inline Python and module constants. This registry is the one
place they are defined. ``tests/test_v17_check_registry.py`` fails CI if any
workflow or module disagrees with it, so a rename or App re-creation is
caught in one PR instead of silently breaking a gate.

Classes:

* ``REQUIRED``: the ruleset-required checks on ``main`` (ruleset 24907422, as
  read 2026-11-18). Only these may block a merge or a deploy.
* ``ADVISORY``: independent QA / Release Authority / security review. Their
  HOLD is recorded, never a merge blocker (owner decision 2026-11-19).
* ``EVIDENCE``: inputs QA reads (change-impact gate). Not ruleset-required.

Pure data. No network. ``can_execute=false``.
"""
from __future__ import annotations

import argparse
import json
import sys

from v17.agent_identity_policy import QA_CHECK, RELEASE_CHECK, REQUIRED_REGRESSION_CHECKS

GITHUB_ACTIONS_APP_ID = 15368
QA_APP_ID = 5245832
RELEASE_APP_ID = 5245853

SECURITY_REVIEW_CHECK = "WOW Independent Security Review"
CHANGE_IMPACT_CHECK = "WOW V17 change impact gate"

REQUIRED: dict[str, int] = {name: GITHUB_ACTIONS_APP_ID for name in REQUIRED_REGRESSION_CHECKS}
ADVISORY: dict[str, int] = {
    QA_CHECK: QA_APP_ID,
    RELEASE_CHECK: RELEASE_APP_ID,
    SECURITY_REVIEW_CHECK: QA_APP_ID,
}
EVIDENCE: dict[str, int] = {CHANGE_IMPACT_CHECK: GITHUB_ACTIONS_APP_ID}
ALL: dict[str, int] = {**REQUIRED, **ADVISORY, **EVIDENCE}

if len(ALL) != len(REQUIRED) + len(ADVISORY) + len(EVIDENCE):  # pragma: no cover - import-time guard
    raise RuntimeError("CHECK_REGISTRY_CLASS_OVERLAP")


def app_id(name: str) -> int:
    """App ID that must publish ``name``; KeyError for an unregistered check."""
    return ALL[name]


def is_required(name: str) -> bool:
    return name in REQUIRED


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--class", dest="klass", choices=("required", "advisory", "evidence", "all"),
                        default="all")
    parser.add_argument("--format", choices=("json", "pairs"), default="json",
                        help="pairs: one 'name|app_id' line per check, for bash consumers")
    args = parser.parse_args(argv)
    table = {"required": REQUIRED, "advisory": ADVISORY, "evidence": EVIDENCE, "all": ALL}[args.klass]
    if args.format == "pairs":
        sys.stdout.write("".join(f"{name}|{app}\n" for name, app in table.items()))
    else:
        print(json.dumps(table, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
