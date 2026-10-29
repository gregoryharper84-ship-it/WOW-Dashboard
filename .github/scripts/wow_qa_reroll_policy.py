#!/usr/bin/env python3
"""Experimental, inactive-by-default security re-roll budget.

Identical-content REJECT protection remains the content-ledger's job and is
never relaxed here. A one-byte file change produces a new Git blob SHA and
therefore a new content digest, which can resample a previous REJECT. A
bounded per-PR attempt policy can limit that resampling while still allowing
a genuine revised PR.

This module does not activate itself. WOW_QA_EXPERIMENTAL_PR_REJECT_BUDGET
must be an explicit positive integer set by an authorized administrator.
Unset, empty, or invalid values keep the policy inactive or fail closed.
"""
from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path

_REVIEW_PATH = Path(__file__).resolve().parent / "wow_simple_security_review.py"
_SPEC = importlib.util.spec_from_file_location("wow_simple_security_review", _REVIEW_PATH)
review = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(review)
Hold = review.Hold
QA_APP_ID = review.QA_APP_ID
CHECK_NAME = review.CHECK_NAME

EXPERIMENTAL_ENV = "WOW_QA_EXPERIMENTAL_PR_REJECT_BUDGET"
MAX_BUDGET = 20
SHA_RE = re.compile(r"^[a-f0-9]{40}$")
DIGEST_RE = re.compile(r"^[a-f0-9]{64}$")


def experimental_budget(raw=None):
    if raw is None:
        raw = os.environ.get(EXPERIMENTAL_ENV, "")
    if raw is None:
        return None
    if type(raw) is not str:
        raise Hold("EXPERIMENTAL_REROLL_BUDGET_INVALID")
    text = raw.strip()
    if not text:
        return None
    if not text.isdigit() or int(text) < 1 or int(text) > MAX_BUDGET:
        raise Hold("EXPERIMENTAL_REROLL_BUDGET_INVALID")
    return int(text)


def content_digest(file_blobs):
    if (type(file_blobs) is not dict or not file_blobs
            or any(type(name) is not str or not name
                   or type(sha) is not str or not SHA_RE.fullmatch(sha)
                   for name, sha in file_blobs.items())):
        raise Hold("REVIEW_CONTENT_IDENTITY_INVALID")
    import hashlib
    import json
    canonical = json.dumps(sorted(file_blobs.items()), separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def one_byte_edit_changes_digest(file_blobs, path, new_blob_sha):
    """Prove a trivial blob change is a new content identity."""
    before = content_digest(file_blobs)
    edited = dict(file_blobs)
    edited[path] = new_blob_sha
    after = content_digest(edited)
    return before, after, before != after


def count_pr_rejects(pr_number, checks):
    if type(pr_number) is not int or pr_number <= 0:
        raise Hold("EXPERIMENTAL_REROLL_PR_INVALID")
    if type(checks) is not list:
        raise Hold("EXPERIMENTAL_REROLL_HISTORY_INVALID")
    counted = 0
    for run in checks:
        if type(run) is not dict:
            raise Hold("EXPERIMENTAL_REROLL_HISTORY_INVALID")
        app = run.get("app") or {}
        title = str((run.get("output") or {}).get("title", ""))
        pr = run.get("pr_number")
        if pr is None:
            pr = run.get("pr")
        if (app.get("id") == QA_APP_ID and run.get("name") == CHECK_NAME
                and run.get("conclusion") == "failure" and title.startswith("REJECT")
                and pr == pr_number):
            counted += 1
    return counted


def identical_content_rejected(digest, checks):
    if type(digest) is not str or not DIGEST_RE.fullmatch(digest):
        raise Hold("EXPERIMENTAL_REROLL_DIGEST_INVALID")
    if type(checks) is not list:
        raise Hold("EXPERIMENTAL_REROLL_HISTORY_INVALID")
    prefix = getattr(review, "LEDGER_NAME_PREFIX", "WOW Semantic Content Ledger ")
    for run in checks:
        if type(run) is not dict:
            raise Hold("EXPERIMENTAL_REROLL_HISTORY_INVALID")
        app = run.get("app") or {}
        title = (run.get("output") or {}).get("title")
        if (app.get("id") == QA_APP_ID and run.get("name") == prefix + digest
                and run.get("status") == "completed"
                and run.get("conclusion") == "failure"
                and title == "REJECT — content review"):
            return True
    return False


def evaluate_reroll_policy(target, checks, *, budget=None):
    """Return None when a new review may proceed.

    Identical-content REJECT is always a terminal Hold, even with the
    experimental budget inactive. The budget, when authorized, bounds how
    many distinct REJECT outcomes one PR may resample before HOLD.
    """
    blobs = target.get("file_blobs")
    digest = content_digest(blobs)
    pr = target.get("pr")
    if identical_content_rejected(digest, checks):
        raise Hold("PREVIOUS_SEMANTIC_REJECT_IDENTICAL_CONTENT")
    active = experimental_budget(budget if budget is not None else os.environ.get(EXPERIMENTAL_ENV, ""))
    if active is None:
        return {"policy": "inactive", "digest": digest, "pr_rejects": count_pr_rejects(pr, checks),
                "allowed": True}
    rejects = count_pr_rejects(pr, checks)
    if rejects >= active:
        raise Hold("EXPERIMENTAL_REROLL_BUDGET_EXHAUSTED")
    return {"policy": "experimental", "digest": digest, "pr_rejects": rejects,
            "budget": active, "allowed": True}
