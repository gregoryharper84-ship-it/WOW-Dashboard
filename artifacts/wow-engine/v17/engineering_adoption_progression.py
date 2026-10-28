"""Typed, read-only terminal outcome for the existing V17 Engineering App adopter.

This does not select, authorize, merge, or deploy a candidate. It prevents an
eligible source from being silently reported as a green skipped-writer run.
"""
from __future__ import annotations

import json
import os
import re

SHA = re.compile(r"^[a-f0-9]{40}$")
PR = re.compile(r"^[1-9][0-9]*$")


class ProgressionHold(ValueError):
    """The existing adoption controller did not complete an authorized transition."""


def classify(*, preflight_result: str, selection_status: str,
             writer_result: str, source_pr: str = "",
             source_sha: str = "") -> dict:
    """One immutable state transition; no credentials, API calls or side effects."""
    if preflight_result == "skipped":
        if writer_result != "skipped" or selection_status or source_pr or source_sha:
            raise ProgressionHold("ADOPTION_INAPPLICABLE_EVENT_INCONSISTENT")
        outcome = "INAPPLICABLE_EVENT"
    elif preflight_result != "success":
        raise ProgressionHold("ADOPTION_PREFLIGHT_UNVERIFIED")
    elif selection_status == "NO_ELIGIBLE_SOURCE":
        if writer_result != "skipped" or source_pr or source_sha:
            raise ProgressionHold("ADOPTION_EMPTY_QUEUE_WRITER_INCONSISTENT")
        outcome = "NO_ELIGIBLE_SOURCE"
    elif selection_status == "SOURCE_READY":
        if not PR.fullmatch(source_pr) or not SHA.fullmatch(source_sha):
            raise ProgressionHold("ADOPTION_ACTIONABLE_SOURCE_IDENTITY_INVALID")
        if writer_result != "success":
            raise ProgressionHold("ADOPTION_WRITER_SKIPPED_WITH_ACTIONABLE_BACKLOG")
        # An App proposal needs independent source review, CODEOWNER and
        # QA/Release. Writer job success NEVER means merged or deployed.
        outcome = "APP_PROPOSAL_REVIEW_PENDING"
    else:
        raise ProgressionHold("ADOPTION_SELECTION_STATUS_UNVERIFIED")
    return {"status": outcome, "writer_result": writer_result, "can_execute": False}


def main() -> None:
    try:
        result = classify(
            preflight_result=os.environ.get("PREFLIGHT_RESULT", ""),
            selection_status=os.environ.get("SELECTION_STATUS", ""),
            writer_result=os.environ.get("WRITER_RESULT", ""),
            source_pr=os.environ.get("SOURCE_PR", ""),
            source_sha=os.environ.get("SOURCE_SHA", ""),
        )
    except ProgressionHold as exc:
        print(f"::error::{exc}")
        raise SystemExit(1) from exc
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
