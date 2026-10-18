"""Fail-closed ordinary Class A auto-merge eligibility (#1021/#1550).

This policy only arms auto-merge; GitHub branch protection remains authoritative.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SHA = re.compile(r"^[a-f0-9]{40}$")
ENGINEERING_LOGIN = "wow-engineering[bot]"


def eligible(pr: dict, files: list[dict], codeowners: str, owner_for_path) -> tuple[bool, str]:
    if not isinstance(pr, dict) or pr.get("state") != "open":
        return False, "PR_NOT_OPEN"
    if pr.get("draft") is not False:
        return False, "PR_DRAFT_OR_UNVERIFIED"
    if pr.get("user", {}).get("login") != ENGINEERING_LOGIN:
        return False, "ENGINEERING_APP_AUTHOR_REQUIRED"
    if pr.get("base", {}).get("ref") != "main":
        return False, "BASE_NOT_MAIN"
    repo = pr.get("base", {}).get("repo", {}).get("full_name")
    if not repo or pr.get("head", {}).get("repo", {}).get("full_name") != repo:
        return False, "FOREIGN_HEAD"
    if not SHA.fullmatch(str(pr.get("head", {}).get("sha", ""))):
        return False, "HEAD_SHA_INVALID"
    labels = {x.get("name") for x in pr.get("labels", []) if isinstance(x, dict)}
    if "class-a" not in labels:
        return False, "CLASS_A_LABEL_REQUIRED"
    if not isinstance(files, list) or not files or len(files) >= 3000:
        return False, "FILE_INVENTORY_MISSING_OR_TRUNCATED"
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("filename"), str):
            return False, "FILE_METADATA_INVALID"
        paths = [item["filename"]]
        if item.get("status") == "renamed":
            if not isinstance(item.get("previous_filename"), str):
                return False, "RENAME_ORIGIN_MISSING"
            paths.append(item["previous_filename"])
        for path in paths:
            if not path or path.startswith((".github/", ".agents/")):
                return False, "PROTECTED_PATH"
            if owner_for_path(codeowners, path):
                return False, "CODEOWNERS_PROTECTED_PATH"
    return True, "ELIGIBLE"


def main() -> int:
    from v17.agent_identity_policy import _codeowners_owners

    root = Path(__file__).resolve().parents[3]
    try:
        pr = json.loads(Path(sys.argv[1]).read_text())
        files = json.loads(Path(sys.argv[2]).read_text())
        codeowners = (root / ".github/CODEOWNERS").read_text()
        decision, reason = eligible(pr, files, codeowners, _codeowners_owners)
    except (IndexError, OSError, ValueError, TypeError, KeyError):
        decision, reason = False, "ELIGIBILITY_EVIDENCE_UNAVAILABLE"
    print(json.dumps({"eligible": decision, "reason": reason, "can_execute": False}))
    return 0 if decision else 3


if __name__ == "__main__":
    raise SystemExit(main())
