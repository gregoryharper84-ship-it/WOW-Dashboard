#!/usr/bin/env python3
"""Protected, read-only OpenAI primary reviewer with typed transport receipts.

Never executes PR code. The review comes from the isolated QA credential;
no Engineering App credentials, check-write token or authorization is used.
Only the protected QA workflow may consume failure receipts as failover input.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import independent_source_review_fallback as fallback

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
OPENAI_MODEL = "gpt-6.1-sol"
PRIMARY_IDENTITY = "openai-responses-api"
PRIMARY_SCOPE = "WOW_QA_REVIEW_OPENAI_API_KEY"
TRANSPORT_CODES = fallback.RETRYABLE_PROVIDER_CODES


def _model_result(prompt: str, key: str) -> str:
    response = fallback._post_json(
        OPENAI_RESPONSES_URL,
        {"authorization": "Bearer " + key, "content-type": "application/json"},
        {
            "model": OPENAI_MODEL,
            "input": [{"role": "user", "content": prompt}],
            "text": {"format": {"name": "wow_independent_source_review",
                                "type": "json_schema", "strict": True,
                                "schema": fallback.REVIEW_SCHEMA}},
            "max_output_tokens": 8192,
            "store": False,
            "tools": [],
        },
    )
    if response.get("status") != "completed":
        raise fallback.ReviewUnavailable("OPENAI_REVIEW_INCOMPLETE")
    output = response.get("output")
    if not isinstance(output, list):
        raise fallback.ReviewUnavailable("OPENAI_REVIEW_OUTPUT_INVALID")
    messages = [item for item in output
                if isinstance(item, dict) and item.get("type") == "message"
                and item.get("role") == "assistant" and item.get("status") == "completed"]
    if len(messages) != 1:
        raise fallback.ReviewUnavailable("OPENAI_REVIEW_OUTPUT_INVALID")
    parts = messages[0].get("content")
    if not isinstance(parts, list) or len(parts) != 1:
        raise fallback.ReviewUnavailable("OPENAI_REVIEW_OUTPUT_INVALID")
    part = parts[0]
    if not isinstance(part, dict) or part.get("type") != "output_text" or not isinstance(part.get("text"), str):
        # A refusal, empty output, or tool call cannot constitute a PASS.
        raise fallback.ReviewUnavailable("OPENAI_REVIEW_REFUSED_OR_MALFORMED")
    return part["text"]


def execute_primary(captured: dict[str, Any], *, key: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(key, str) or not key:
        raise fallback.ReviewUnavailable("PRIMARY_CREDENTIAL_MISSING")
    parsed = fallback._validate_reply(_model_result(fallback._prompt(captured), key), captured)
    return parsed, {"provider_identity": PRIMARY_IDENTITY,
                    "credential_scope": PRIMARY_SCOPE, "can_execute": False}


def _failure_receipt(reason: str, captured: dict[str, Any]) -> dict[str, Any]:
    pr = captured["pr"]
    code = reason
    # Only exact transport codes are eligible. Unknown, semantic, 403 and
    # malformed errors are terminal; never model-decided retry permissions.
    return {
        "provider_identity": PRIMARY_IDENTITY,
        "credential_scope": PRIMARY_SCOPE,
        "reviewed_head_sha": pr["head"]["sha"],
        "reviewed_diff_sha256": captured["diff_sha256"],
        "typed_error": code,
        "fallback_eligible": code == "PRIMARY_CREDENTIAL_MISSING" or code in TRANSPORT_CODES,
        "review_approved": False,
        "can_execute": False,
    }


def validate_failure_receipt(receipt: object, captured: dict[str, Any]) -> bool:
    if not isinstance(receipt, dict) or set(receipt) != {
        "provider_identity", "credential_scope", "reviewed_head_sha",
        "reviewed_diff_sha256", "typed_error", "fallback_eligible",
        "review_approved", "can_execute",
    }:
        raise fallback.ReviewUnavailable("PRIMARY_FAILURE_RECEIPT_INVALID")
    allowed = receipt["typed_error"] == "PRIMARY_CREDENTIAL_MISSING" or (
        isinstance(receipt["typed_error"], str)
        and receipt["typed_error"] in TRANSPORT_CODES
    )
    if not (
        receipt["provider_identity"] == PRIMARY_IDENTITY
        and receipt["credential_scope"] == PRIMARY_SCOPE
        and receipt["reviewed_head_sha"] == captured["pr"]["head"]["sha"]
        and receipt["reviewed_diff_sha256"] == captured["diff_sha256"]
        and receipt["fallback_eligible"] is allowed
        and receipt["review_approved"] is False
        and receipt["can_execute"] is False
    ):
        raise fallback.ReviewUnavailable("PRIMARY_FAILURE_RECEIPT_MISMATCH")
    return allowed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--provenance", type=Path)
    parser.add_argument("--failure", type=Path, required=True)
    parser.add_argument("--verify-failure", action="store_true")
    args = parser.parse_args(argv)
    try:
        captured = fallback._read_input(args.input)
    except fallback.ReviewUnavailable as exc:
        print(json.dumps({"status": "HOLD", "typed_error": str(exc)}), file=sys.stderr)
        return 2
    if args.verify_failure:
        try:
            receipt = json.loads(args.failure.read_text(encoding="utf-8"))
            ok = validate_failure_receipt(receipt, captured)
        except (OSError, ValueError, json.JSONDecodeError, fallback.ReviewUnavailable):
            print("PRIMARY_FAILURE_RECEIPT_INVALID", file=sys.stderr)
            return 2
        print("true" if ok else "false")
        return 0
    if args.result is None or args.provenance is None:
        print("PRIMARY_OUTPUT_PATHS_MISSING", file=sys.stderr)
        return 2
    try:
        result, proof = execute_primary(
            captured, key=os.environ.get("OPENAI_REVIEW_KEY", ""),
        )
    except fallback.ReviewUnavailable as exc:
        receipt = _failure_receipt(str(exc), captured)
        args.failure.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"status": "HOLD", "typed_error": str(exc),
                          "fallback_eligible": receipt["fallback_eligible"]}), file=sys.stderr)
        return 2
    args.result.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    args.provenance.write_text(json.dumps(proof, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "REVIEW_REAL_VERDICT", "provider_identity": PRIMARY_IDENTITY,
                      "decision": result["decision"], "can_execute": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
