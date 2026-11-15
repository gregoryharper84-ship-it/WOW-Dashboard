#!/usr/bin/env python3
"""Attributable Gemini PR-review canary using a distinct least-privilege GitHub App.

Manual-only legacy diagnostic. This never publishes required QA/Release acceptance,
merges code, or changes fitted probabilities. Executes protected-main code only.
"""
import json
import os
import re
import sys
from pathlib import Path

import wow_simple_security_review as semantic

REPO = "gregoryharper84-ship-it/WOW-Dashboard"
APP_ID = "5765620"
BOT_LOGIN = "wow-independent-reviewer[bot]"
PREFIX = "WOW_V28_GEMINI_REVIEW_V1"
SHA = re.compile(r"^[a-f0-9]{40}$")
DIGEST = re.compile(r"^[a-f0-9]{64}$")


class AttestationHold(ValueError):
    pass


def trusted_receipt(receipt, *, run_id, attempt):
    if not isinstance(receipt, dict):
        raise AttestationHold("GEMINI_REVIEW_RECEIPT_INVALID")
    if (receipt.get("schema_version") != semantic.REVIEW_CONTRACT_VERSION
            or receipt.get("repository") != REPO
            or receipt.get("reviewer_identity") != "gemini-generative-api"
            or receipt.get("decision") not in ("PASS", "REJECT")
            or receipt.get("can_execute") is not False
            or receipt.get("no_live_trading") is not True
            or receipt.get("run_id") != run_id
            or receipt.get("run_attempt") != attempt):
        raise AttestationHold("GEMINI_REVIEW_IDENTITY_OR_PROVENANCE_INVALID")
    sha, digest, number = receipt.get("head_sha"), receipt.get("diff_sha256"), receipt.get("pr_number")
    if (not isinstance(sha, str) or SHA.fullmatch(sha) is None
            or not isinstance(digest, str) or DIGEST.fullmatch(digest) is None
            or type(number) is not int or number <= 0):
        raise AttestationHold("GEMINI_REVIEW_TARGET_INVALID")
    # Semantic parser already checked the provider's bounded four-domain verdict.
    # This step refuses synthetic or partial output from a missing provider.
    if not isinstance(receipt.get("verdict"), dict):
        raise AttestationHold("GEMINI_REVIEW_VERDICT_MISSING")
    return (f"{PREFIX} PR={number} HEAD={sha} DIFF_SHA256={digest} "
            f"DECISION={receipt['decision']} RUN={run_id}/{attempt}")


def matching_reviews(reviews, sha):
    return [r for r in reviews if isinstance(r, dict)
            and (r.get("user") or {}).get("login") == BOT_LOGIN
            and r.get("commit_id") == sha]


def review_event(receipt, body, reviews):
    own = matching_reviews(reviews, receipt["head_sha"])
    if any(r.get("state") == "CHANGES_REQUESTED" for r in own) and receipt["decision"] == "PASS":
        raise AttestationHold("GEMINI_REVIEW_STICKY_REJECT_SAME_SHA")
    wanted = "APPROVED" if receipt["decision"] == "PASS" else "CHANGES_REQUESTED"
    exact = [r for r in own if r.get("state") == wanted and r.get("body") == body]
    if len(exact) > 1:
        raise AttestationHold("GEMINI_REVIEW_DUPLICATE_ATTESTATION")
    if exact:
        return None
    if any(r.get("state") in ("APPROVED", "CHANGES_REQUESTED") for r in own):
        raise AttestationHold("GEMINI_REVIEW_CONFLICTING_SAME_SHA")
    return "APPROVE" if receipt["decision"] == "PASS" else "REQUEST_CHANGES"


def read_reviews(number, read_token):
    rows = []
    for page in range(1, 11):
        batch = semantic.gh(f"/pulls/{number}/reviews?per_page=100&page={page}", read_token)
        if not isinstance(batch, list):
            raise AttestationHold("GEMINI_REVIEW_INVENTORY_UNREADABLE")
        rows.extend(batch)
        if len(batch) < 100:
            return rows
    raise AttestationHold("GEMINI_REVIEW_INVENTORY_LIMIT_EXCEEDED")


def run():
    if (os.environ.get("GITHUB_REPOSITORY") != REPO
            or os.environ.get("GITHUB_REF") != "refs/heads/main"
            or os.environ.get("WOW_REVIEWER_APP_ID") != APP_ID):
        raise AttestationHold("GEMINI_REVIEW_TRUSTED_CONTEXT_MISSING")
    read_token = os.environ.get("GH_READ_TOKEN", "")
    app_token = os.environ.get("REVIEWER_APP_TOKEN", "")
    if not read_token or not app_token:
        raise AttestationHold("GEMINI_REVIEW_AUTHENTICATION_MISSING")
    receipt_path = Path("wow-independent-security-decision.json")
    if not receipt_path.is_file():
        raise AttestationHold("GEMINI_REVIEW_RECEIPT_MISSING")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    number = int(os.environ["GITHUB_RUN_ID"])
    attempt = int(os.environ["GITHUB_RUN_ATTEMPT"])
    body = trusted_receipt(receipt, run_id=number, attempt=attempt)
    pr = receipt["pr_number"]
    target = semantic.capture(pr, receipt["head_sha"], read_token)
    semantic.require_live_identity(semantic.gh(f"/pulls/{pr}", read_token), target)
    if (target["author"] == BOT_LOGIN
            or target["diff_sha256"] != receipt["diff_sha256"]
            or target["base_sha"] != receipt["base_sha"]):
        raise AttestationHold("GEMINI_REVIEW_SOURCE_MOVED_OR_AUTHOR_INVALID")
    event = review_event(receipt, body, read_reviews(pr, read_token))
    if event is None:
        print("GEMINI_APP_REVIEW_ALREADY_VERIFIED")
        return
    response = semantic.request_json(
        "https://api.github.com/repos/" + REPO + f"/pulls/{pr}/reviews",
        token=app_token, data={"commit_id": target["head_sha"], "event": event, "body": body},
    )
    if (not isinstance(response, dict)
            or response.get("commit_id") != target["head_sha"]
            or response.get("body") != body
            or (response.get("user") or {}).get("login") != BOT_LOGIN):
        raise AttestationHold("GEMINI_APP_REVIEW_IDENTITY_UNVERIFIED")
    print("GEMINI_APP_REVIEW_ATTESTED_AT_EXACT_HEAD")


if __name__ == "__main__":
    try:
        run()
    except (AttestationHold, semantic.Hold) as error:
        print("::error::" + str(error), file=sys.stderr)
        raise SystemExit(1)
    except (OSError, ValueError, KeyError, TypeError):
        print("::error::GEMINI_REVIEW_ATTESTATION_HOLD", file=sys.stderr)
        raise SystemExit(1)
