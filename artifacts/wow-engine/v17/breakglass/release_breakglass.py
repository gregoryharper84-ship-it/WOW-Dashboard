#!/usr/bin/env python3
"""WOW V17 independent, owner+SIRT signed, out-of-band maintenance recovery.

This CLI is deliberately NOT a GitHub Action or a competing Release publisher.
It runs from an administrator-controlled workstation; preflight is read-only.
A separate human/operator-controlled RESTORATION WITNESS must be active before
execution because no local finally block can survive process/machine death.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from typing import Any

CANONICAL_REPO = "gregoryharper84-ship-it/WOW-Dashboard"
RULESET_ID = 24907422
SHA = re.compile(r"^[0-9a-f]{40}$")
RULESET_FIELDS = ("name", "target", "enforcement", "conditions", "rules", "bypass_actors")
CHECK_CONTEXTS = (
    ("WOW required-three regression", 15368),
    ("WOW governed probability backend", 15368),
    ("WOW additional required regression", 15368),
    ("WOW Independent QA exact-head", 5245832),
    ("WOW Release Authority exact-head", 5245853),
)
MAX_PAGES = 15
PER_PAGE = 100
MAX_WINDOW_SECONDS = 120
PINNED_OWNER_KEY_FINGERPRINT = "SHA256:BGiNe5ggZrgozxmFBXsFEEyn8szHPHFmZSCN5nJ/+SM"


class RecoveryHold(Exception):
    pass


def require(value: Any, code: str) -> None:
    if not value:
        raise RecoveryHold(code)


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def run(*argv: str, input_bytes: bytes | None = None, timeout: int = 25) -> bytes:
    try:
        proc = subprocess.run(argv, input=input_bytes, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RecoveryHold("EXTERNAL_COMMAND_UNAVAILABLE_OR_TIMEOUT") from exc
    require(proc.returncode == 0, "EXTERNAL_COMMAND_FAILED:" + argv[0] + ":" + proc.stderr[:160].decode("utf-8", "replace"))
    return proc.stdout


class GitHub:
    def __init__(self, repo: str = CANONICAL_REPO):
        require(repo == CANONICAL_REPO, "REPO_NOT_ALLOWED")
        self.repo = repo

    def get(self, suffix: str) -> Any:
        return json.loads(run("gh", "api", f"repos/{self.repo}/{suffix}"))

    def put(self, suffix: str, payload: dict) -> Any:
        return json.loads(run("gh", "api", "--method", "PUT", f"repos/{self.repo}/{suffix}", "--input", "-", input_bytes=canonical(payload)))

    def merge(self, pr: int, head: str) -> Any:
        return json.loads(run("gh", "api", "--method", "PUT", f"repos/{self.repo}/pulls/{pr}/merge",
                              "-f", "merge_method=merge", "-f", f"sha={head}"))

    def pages(self, suffix: str, collection: str | None = None) -> list[dict]:
        all_rows: list[dict] = []
        for page in range(1, MAX_PAGES + 1):
            delimiter = "&" if "?" in suffix else "?"
            doc = self.get(f"{suffix}{delimiter}per_page={PER_PAGE}&page={page}")
            rows = doc.get(collection) if collection is not None and isinstance(doc, dict) else doc
            require(isinstance(rows, list), "GITHUB_PAGINATION_SHAPE_INVALID")
            all_rows.extend(rows)
            if len(rows) < PER_PAGE:
                if collection is not None:
                    require(doc.get("total_count") == len(all_rows), "GITHUB_PAGINATION_TRUNCATED")
                return all_rows
        raise RecoveryHold("GITHUB_PAGINATION_LIMIT")


def parse_intent(raw: Any, now: dt.datetime | None = None) -> dict:
    require(isinstance(raw, dict), "INTENT_NOT_OBJECT")
    keys = {"schema", "repo", "ruleset_id", "pr", "head_sha", "main_sha", "incident",
            "reviewer_login", "expires_utc", "allowed_files", "baseline_ruleset_sha256",
            "removed_check", "window_seconds"}
    require(set(raw) == keys, "INTENT_SCHEMA_MISMATCH")
    require(raw["schema"] == "WOW_BREAKGLASS_V1" and raw["repo"] == CANONICAL_REPO, "INTENT_IDENTITY_INVALID")
    require(type(raw["ruleset_id"]) is int and raw["ruleset_id"] == RULESET_ID, "INTENT_RULESET_INVALID")
    require(type(raw["pr"]) is int and raw["pr"] > 0, "INTENT_PR_INVALID")
    require(type(raw["incident"]) is int and raw["incident"] > 0, "INTENT_INCIDENT_INVALID")
    for field in ("head_sha", "main_sha"):
        require(isinstance(raw[field], str) and SHA.fullmatch(raw[field]), f"INTENT_{field.upper()}_INVALID")
    require(raw["removed_check"] == list(CHECK_CONTEXTS[-1]), "INTENT_REMOVED_CHECK_NOT_ALLOWED")
    require(type(raw["window_seconds"]) is int and 15 <= raw["window_seconds"] <= MAX_WINDOW_SECONDS, "INTENT_WINDOW_INVALID")
    files = raw["allowed_files"]
    require(isinstance(files, list) and 1 <= len(files) <= 8 and len(files) == len(set(files)), "INTENT_FILES_INVALID")
    require(all(isinstance(f, str) and (f.startswith(".github/") or f.startswith("artifacts/wow-engine/"))
                and ".." not in Path(f).parts for f in files), "INTENT_FILES_OUTSIDE_MAINTENANCE_SCOPE")
    require(isinstance(raw["baseline_ruleset_sha256"], str)
            and re.fullmatch(r"[0-9a-f]{64}", raw["baseline_ruleset_sha256"]), "INTENT_RULESET_DIGEST_INVALID")
    reviewer = raw["reviewer_login"]
    require(isinstance(reviewer, str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", reviewer))
            and reviewer.lower() not in {"gregoryharper84-ship-it", "copilot", "copilot-swe-agent[bot]"},
            "INTENT_REVIEWER_NOT_INDEPENDENT")
    try:
        expiry = dt.datetime.fromisoformat(raw["expires_utc"].replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise RecoveryHold("INTENT_EXPIRY_INVALID") from exc
    clock = now or dt.datetime.now(dt.timezone.utc)
    require(expiry.utcoffset() == dt.timedelta(0) and 0 < (expiry - clock).total_seconds() <= 3600,
            "INTENT_EXPIRED_OR_TOO_LONG")
    return raw


def verify_signature(intent_bytes: bytes, sig_path: Path, signers_path: Path, principal: str) -> None:
    require(sig_path.is_file() and signers_path.is_file(), "SIGNATURE_OR_TRUST_ANCHOR_MISSING")
    text = signers_path.read_text(encoding="utf-8")
    require(any(line.startswith(principal + " ") and 'namespaces="wow-breakglass"' in line
                for line in text.splitlines()), "SIGNER_NOT_REGISTERED_FOR_BREAKGLASS")
    run("ssh-keygen", "-Y", "verify", "-f", str(signers_path), "-I", principal,
        "-n", "wow-breakglass", "-s", str(sig_path), input_bytes=intent_bytes)


def verify_separate_principals(owner_file: Path, sirt_file: Path, reviewer: str) -> None:
    owner = [x for x in owner_file.read_text(encoding="utf-8").splitlines() if x.startswith("wow-owner ")]
    sirt = [x for x in sirt_file.read_text(encoding="utf-8").splitlines() if x.startswith("wow-sirt-" + reviewer + " ")]
    require(len(owner) == len(sirt) == 1, "SIGNER_RECORDS_MISSING_OR_DUPLICATE")
    def key_data(s: str) -> str:
        v = s.split()
        return next((v[i + 1] for i, x in enumerate(v[:-1]) if x in {"ssh-ed25519", "ssh-rsa", "sk-ssh-ed25519@openssh.com"}), "")
    require(key_data(owner[0]) and key_data(sirt[0]) and key_data(owner[0]) != key_data(sirt[0]),
            "OWNER_AND_SIRT_PUBLIC_KEYS_NOT_DISTINCT")
    # Owner key was registered in protected main before the current deadlock.
    # An arbitrary --owner-signers file MUST NOT silently redefine that trust root.
    try:
        fingerprint = "SHA256:" + base64.b64encode(
            hashlib.sha256(base64.b64decode(key_data(owner[0]), validate=True)).digest()
        ).decode("ascii").rstrip("=")
    except (ValueError, base64.binascii.Error) as exc:
        raise RecoveryHold("OWNER_SIGNER_KEY_INVALID") from exc
    require(fingerprint == PINNED_OWNER_KEY_FINGERPRINT, "OWNER_SIGNER_FINGERPRINT_NOT_PINNED")


def witness_ready_payload(intent: dict, witness_login: str, *, issued: dt.datetime | None = None, pid: int | None = None) -> dict:
    """Cryptographically attest that a distinct operator armed the watcher.

    This is a short-lived start receipt, NOT a proof that the process can never
    crash; the separately operated watchdog and exclusive maintenance window
    remain operational prerequisites.
    """
    t = issued or dt.datetime.now(dt.timezone.utc)
    require(t.tzinfo is not None and t.utcoffset() == dt.timedelta(0), "WITNESS_CLOCK_INVALID")
    require(isinstance(witness_login, str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", witness_login)),
            "WITNESS_LOGIN_INVALID")
    require(type(pid or os.getpid()) is int and (pid or os.getpid()) > 0, "WITNESS_PID_INVALID")
    return {"schema": "WOW_BREAKGLASS_WITNESS_READY_V1", "repo": CANONICAL_REPO,
            "intent_sha256": sha256(intent), "baseline_ruleset_sha256": intent["baseline_ruleset_sha256"],
            "reviewer_login": intent["reviewer_login"], "witness_login": witness_login,
            "witness_pid": pid or os.getpid(),
            "issued_utc": t.isoformat().replace("+00:00", "Z"),
            "expires_utc": (t + dt.timedelta(seconds=min(intent["window_seconds"], 90))).isoformat().replace("+00:00", "Z")}


def verify_witness_ready(intent: dict, ready_path: Path, ready_signature: Path,
                         sirt_signers: Path, executor_login: str, *,
                         now: dt.datetime | None = None) -> dict:
    """Require a freshly signed, separately identified watchdog start receipt."""
    require(ready_path.is_file(), "WITNESS_READY_RECEIPT_MISSING")
    raw = ready_path.read_bytes()
    require(len(raw) <= 4096, "WITNESS_READY_RECEIPT_OVERSIZED")
    try:
        proof = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise RecoveryHold("WITNESS_READY_RECEIPT_INVALID") from exc
    require(isinstance(proof, dict) and set(proof) == {
        "schema", "repo", "intent_sha256", "baseline_ruleset_sha256", "reviewer_login",
        "witness_login", "witness_pid", "issued_utc", "expires_utc"
    }, "WITNESS_READY_RECEIPT_SCHEMA_INVALID")
    require(raw == canonical(proof), "WITNESS_READY_RECEIPT_NOT_CANONICAL")
    require(proof["schema"] == "WOW_BREAKGLASS_WITNESS_READY_V1"
            and proof["repo"] == CANONICAL_REPO
            and proof["intent_sha256"] == sha256(intent)
            and proof["baseline_ruleset_sha256"] == intent["baseline_ruleset_sha256"]
            and proof["reviewer_login"] == intent["reviewer_login"],
            "WITNESS_READY_INTENT_MISMATCH")
    witness_login = proof["witness_login"]
    require(isinstance(witness_login, str)
            and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", witness_login))
            and isinstance(executor_login, str)
            and witness_login.lower() != executor_login.lower(),
            "WITNESS_AND_EXECUTOR_NOT_SEPARATE")
    require(type(proof["witness_pid"]) is int and proof["witness_pid"] > 0, "WITNESS_PID_INVALID")
    try:
        issued = dt.datetime.fromisoformat(proof["issued_utc"].replace("Z", "+00:00"))
        expires = dt.datetime.fromisoformat(proof["expires_utc"].replace("Z", "+00:00"))
        intent_expires = dt.datetime.fromisoformat(intent["expires_utc"].replace("Z", "+00:00"))
    except (TypeError, AttributeError, ValueError) as exc:
        raise RecoveryHold("WITNESS_READY_TIME_INVALID") from exc
    clock = now or dt.datetime.now(dt.timezone.utc)
    require(issued.utcoffset() == expires.utcoffset() == dt.timedelta(0)
            and issued <= clock <= expires and 0 < (expires - issued).total_seconds() <= 90
            and expires <= intent_expires, "WITNESS_READY_EXPIRED_OR_FUTURE")
    verify_signature(raw, ready_signature, sirt_signers, "wow-sirt-" + intent["reviewer_login"])
    return proof


def expected_check_set() -> set[tuple[str, int]]:
    return set(CHECK_CONTEXTS)


def status_rule(rs: dict) -> dict:
    require(isinstance(rs, dict) and all(k in rs for k in RULESET_FIELDS), "RULESET_SNAPSHOT_INCOMPLETE")
    require(rs.get("target") == "branch" and rs.get("enforcement") == "active"
            and rs.get("bypass_actors") == [] and isinstance(rs.get("rules"), list),
            "RULESET_NOT_ENFORCED_OR_BYPASSED")
    require(rs.get("conditions") == {"ref_name": {"exclude": [], "include": ["~DEFAULT_BRANCH"]}},
            "RULESET_BRANCH_SCOPE_CHANGED")
    kinds = {r.get("type") for r in rs["rules"] if isinstance(r, dict)}
    require({"deletion", "non_fast_forward", "pull_request", "required_status_checks"} <= kinds,
            "RULESET_PROTECTION_TYPES_MISSING")
    status = [r for r in rs["rules"] if r.get("type") == "required_status_checks"]
    require(len(status) == 1 and isinstance(status[0].get("parameters"), dict), "RULESET_STATUS_RULE_AMBIGUOUS")
    return status[0]


def checks_from_ruleset(rs: dict) -> set[tuple[str, int]]:
    checks = status_rule(rs)["parameters"].get("required_status_checks")
    require(isinstance(checks, list), "RULESET_CHECKS_UNREADABLE")
    require(all(isinstance(x.get("integration_id"), int) and isinstance(x.get("context"), str) for x in checks),
            "RULESET_CHECK_IDENTITY_INVALID")
    require(len(checks) == len(set((x["context"], x["integration_id"]) for x in checks)),
            "RULESET_CHECK_DUPLICATE")
    return {(x["context"], x["integration_id"]) for x in checks}


def snapshot(rs: dict) -> dict:
    return {k: rs[k] for k in RULESET_FIELDS}


def temporary_ruleset(rs: dict) -> dict:
    require(checks_from_ruleset(rs) == expected_check_set(), "RULESET_NOT_EXACT_FIVE_CHECK_BASELINE")
    new = json.loads(json.dumps(snapshot(rs)))
    c = status_rule(new)["parameters"]["required_status_checks"]
    status_rule(new)["parameters"]["required_status_checks"] = [
        x for x in c if (x["context"], x["integration_id"]) != CHECK_CONTEXTS[-1]]
    require(checks_from_ruleset(new) == expected_check_set() - {CHECK_CONTEXTS[-1]},
            "TEMPORARY_RULES_NOT_EXACT_FOUR_CHECKS")
    return new


def verify_latest_app_checks(rows: list[dict], head: str) -> None:
    # Check-run list must be complete. Select newest by GitHub check ID, not
    # by a non-authoritative reviewer-submitted comment or old green result.
    for name, app_id in CHECK_CONTEXTS[:-1]:
        exact = [x for x in rows if x.get("name") == name and x.get("head_sha") == head
                 and (x.get("app") or {}).get("id") == app_id]
        require(exact, "REQUIRED_APP_CHECK_MISSING:" + name)
        require(all(type(x.get("id")) is int for x in exact), "APP_CHECK_ID_INVALID")
        latest = max(exact, key=lambda x: x["id"])
        require(latest.get("status") == "completed" and latest.get("conclusion") == "success",
                "LATEST_REQUIRED_APP_CHECK_NOT_SUCCESS:" + name)


def verify_native_review(reviews: list[dict], intent: dict) -> None:
    target = intent["reviewer_login"].lower()
    matching = [r for r in reviews if (r.get("user") or {}).get("login", "").lower() == target]
    require(matching, "NATIVE_INDEPENDENT_REVIEW_MISSING")
    require(all(type(x.get("id")) is int for x in matching), "NATIVE_REVIEW_IDS_INVALID")
    latest = max(matching, key=lambda x: x["id"])
    body = latest.get("body") or ""
    require(latest.get("state") == "APPROVED" and latest.get("commit_id") == intent["head_sha"]
            and all(v in body for v in ("SIRT_SECURITY_PASS", intent["head_sha"],
                                      str(intent["ruleset_id"]), intent["baseline_ruleset_sha256"])),
            "NATIVE_INDEPENDENT_SECURITY_APPROVAL_INVALID")


def preflight(api: GitHub, intent: dict) -> dict:
    pr = api.get(f"pulls/{intent['pr']}")
    require(pr.get("state") == "open" and pr.get("draft") is False, "PR_NOT_READY")
    require((pr.get("head") or {}).get("sha") == intent["head_sha"]
            and ((pr.get("head") or {}).get("repo") or {}).get("full_name") == CANONICAL_REPO,
            "PR_HEAD_MOVED_OR_FORK")
    require((pr.get("base") or {}).get("ref") == "main", "PR_BASE_INVALID")
    require((pr.get("user") or {}).get("login", "").lower() != intent["reviewer_login"].lower(),
            "PR_AUTHOR_CANNOT_BE_SIRT_REVIEWER")
    require(api.get("git/ref/heads/main")["object"]["sha"] == intent["main_sha"], "MAIN_MOVED")
    rs_live = api.get(f"rulesets/{intent['ruleset_id']}")
    rs = snapshot(rs_live)
    require(sha256(rs) == intent["baseline_ruleset_sha256"], "RULESET_DIGEST_DRIFT")
    require(checks_from_ruleset(rs) == expected_check_set(), "EXPECTED_FIVE_CHECKS_MISSING")
    files = api.pages(f"pulls/{intent['pr']}/files")
    require(len(files) == pr.get("changed_files"), "PR_FILE_INVENTORY_INCOMPLETE")
    paths: list[str] = []
    for row in files:
        require(isinstance(row, dict) and isinstance(row.get("filename"), str), "PR_FILE_ENTRY_INVALID")
        paths.append(row["filename"])
        if row.get("status") == "renamed":
            require(isinstance(row.get("previous_filename"), str), "PR_RENAME_ORIGIN_MISSING")
            paths.append(row["previous_filename"])
    require(set(paths) == set(intent["allowed_files"]), "PR_FILE_SCOPE_CHANGED")
    verify_latest_app_checks(api.pages(f"commits/{intent['head_sha']}/check-runs", "check_runs"), intent["head_sha"])
    reviews = api.pages(f"pulls/{intent['pr']}/reviews")
    verify_native_review(reviews, intent)
    owner = [r for r in reviews if (r.get("user") or {}).get("login") == "gregoryharper84-ship-it"]
    require(owner and max(owner, key=lambda x: x.get("id", 0)).get("state") == "APPROVED"
            and max(owner, key=lambda x: x.get("id", 0)).get("commit_id") == intent["head_sha"],
            "OWNER_EXACT_HEAD_REVIEW_MISSING")
    armed = [x.get("number") for x in api.pages("pulls?state=open") if x.get("auto_merge") is not None]
    require(not armed, "OTHER_PR_AUTO_MERGE_ARMED")
    return rs


def save_private(path: Path, data: bytes) -> None:
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def restore(api: GitHub, baseline: dict) -> None:
    require(checks_from_ruleset(baseline) == expected_check_set(), "RESTORE_BASELINE_INVALID")
    error = ""
    for _ in range(3):
        try:
            api.put(f"rulesets/{RULESET_ID}", baseline)
            actual = snapshot(api.get(f"rulesets/{RULESET_ID}"))
            require(sha256(actual) == sha256(baseline), "RESTORE_READBACK_MISMATCH")
            print("FULL_FIVE_CHECK_RULESET_RESTORED_AND_VERIFIED", flush=True)
            return
        except (RecoveryHold, OSError, KeyError, TypeError) as exc:
            error = str(exc)
            print("CRITICAL: restoration attempt failed: " + error, file=sys.stderr, flush=True)
            time.sleep(1)
    raise RecoveryHold("P0_RESTORE_FAILED_WITNESS_MUST_RESTORE:" + error)


def execute(api: GitHub, intent: dict, checkpoint: Path, *, verify_witness: Any = None) -> None:
    baseline = preflight(api, intent)
    require(callable(verify_witness), "WITNESS_ATTESTATION_REQUIRED")
    verify_witness()
    require(not checkpoint.exists(), "CHECKPOINT_ALREADY_EXISTS")
    checkpoint.mkdir(parents=True, mode=0o700)
    save_private(checkpoint / "original-ruleset.json", canonical(baseline))
    save_private(checkpoint / "intent.json", canonical(intent))
    save_private(checkpoint / "original-ruleset.sha256", (sha256(baseline) + "\n").encode())
    print("RESTORE_NOW: python3 release_breakglass.py restore --baseline", checkpoint / "original-ruleset.json", flush=True)
    # Re-read immediately before write. A signature doesn't make GitHub state immutable.
    require(sha256(preflight(api, intent)) == sha256(baseline), "PREWRITE_DRIFT")
    verify_witness()  # freshness MUST still hold at the mutation boundary
    old_handlers = {}
    def interrupted(signum, _frame):
        raise RecoveryHold("SIGNAL_INTERRUPTED:" + str(signum))
    for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        old_handlers[s] = signal.signal(s, interrupted)
    first_mutation_attempted = False
    error = None
    start = time.monotonic()
    try:
        first_mutation_attempted = True  # ambiguous HTTP timeouts must restore
        temporary = temporary_ruleset(baseline)
        api.put(f"rulesets/{RULESET_ID}", temporary)
        require(sha256(snapshot(api.get(f"rulesets/{RULESET_ID}"))) == sha256(temporary), "FOUR_CHECK_READBACK_FAILED")
        require(time.monotonic() - start < intent["window_seconds"], "MERGE_WINDOW_EXPIRED")
        require(api.get("git/ref/heads/main")["object"]["sha"] == intent["main_sha"], "MAIN_MOVED_IN_WINDOW")
        require(api.get(f"pulls/{intent['pr']}")["head"]["sha"] == intent["head_sha"], "PR_HEAD_MOVED_IN_WINDOW")
        response = api.merge(intent["pr"], intent["head_sha"])
        require(response.get("merged") is True, "GITHUB_PROTECTED_MERGE_NOT_CONFIRMED")
        save_private(checkpoint / "merge-receipt.json", canonical(response))
    except (RecoveryHold, OSError, ValueError, KeyError, TypeError) as exc:
        error = str(exc)
    finally:
        try:
            if first_mutation_attempted:
                restore(api, baseline)
        finally:
            for s, handler in old_handlers.items():
                signal.signal(s, handler)
    require(error is None, "MERGE_UNCONFIRMED:" + str(error))
    require(api.get(f"pulls/{intent['pr']}").get("merged") is True, "MERGE_READBACK_FALSE")
    print("EXACT_HEAD_MERGED; RULESET_RESTORED; NEW_MAIN_QA_RELEASE_STILL_REQUIRED")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    inspect = sub.add_parser("inspect", help="Read-only snapshot and candidate receipt")
    inspect.add_argument("--pr", type=int, required=True)
    prep = sub.add_parser("prepare", help="Generate signable narrow recovery intent; no mutation")
    prep.add_argument("--pr", type=int, required=True)
    prep.add_argument("--incident", type=int, required=True)
    prep.add_argument("--reviewer", required=True)
    prep.add_argument("--allowed-file", action="append", required=True)
    prep.add_argument("--output", type=Path, required=True)
    go = sub.add_parser("execute", help="Two offline signatures + native review + witnessed recovery")
    go.add_argument("--intent", type=Path, required=True)
    go.add_argument("--owner-signature", type=Path, required=True)
    go.add_argument("--sirt-signature", type=Path, required=True)
    go.add_argument("--owner-signers", type=Path, required=True)
    go.add_argument("--sirt-signers", type=Path, required=True)
    go.add_argument("--checkpoint", type=Path, required=True)
    go.add_argument("--witness-ready", type=Path, required=True)
    go.add_argument("--witness-ready-signature", type=Path, required=True)
    go.add_argument("--exclusive-window-verified", action="store_true", required=True)
    restore_parser = sub.add_parser("restore", help="Independent witness restoration, even if operator host fails")
    restore_parser.add_argument("--baseline", type=Path, required=True)
    args = parser.parse_args()
    api = GitHub()
    if args.mode == "inspect":
        p = api.get(f"pulls/{args.pr}")
        rs = snapshot(api.get(f"rulesets/{RULESET_ID}"))
        print(json.dumps({"repo": CANONICAL_REPO, "pr": args.pr, "head": p["head"]["sha"],
                          "main": api.get("git/ref/heads/main")["object"]["sha"],
                          "ruleset_sha256": sha256(rs), "required_checks": sorted(checks_from_ruleset(rs)),
                          "can_execute": False}, indent=2))
    elif args.mode == "prepare":
        p = api.get(f"pulls/{args.pr}")
        rs = snapshot(api.get(f"rulesets/{RULESET_ID}"))
        require(checks_from_ruleset(rs) == expected_check_set(), "BASELINE_NOT_FIVE_CHECKS")
        intent = {"schema": "WOW_BREAKGLASS_V1", "repo": CANONICAL_REPO,
                  "ruleset_id": RULESET_ID, "pr": args.pr, "head_sha": p["head"]["sha"],
                  "main_sha": api.get("git/ref/heads/main")["object"]["sha"],
                  "incident": args.incident, "reviewer_login": args.reviewer,
                  "expires_utc": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=45)).isoformat().replace("+00:00", "Z"),
                  "allowed_files": args.allowed_file, "baseline_ruleset_sha256": sha256(rs),
                  "removed_check": list(CHECK_CONTEXTS[-1]), "window_seconds": 90}
        parse_intent(intent)
        require(not args.output.exists(), "OUTPUT_EXISTS")
        baseline = args.output.with_suffix(".baseline.json")
        require(not baseline.exists(), "BASELINE_OUTPUT_EXISTS")
        save_private(baseline, canonical(rs))
        save_private(args.output, canonical(intent))
        print("OFFLINE_INTENT_FOR_BOTH_SIGNATURES", args.output)
        print("COPY_BASELINE_TO_INDEPENDENT_WITNESS_MACHINE", baseline)
    elif args.mode == "restore":
        raw = json.loads(args.baseline.read_bytes())
        require(set(raw) == set(RULESET_FIELDS), "UNTRUSTED_RESTORE_FILE")
        restore(api, raw)
    else:
        raw_bytes = args.intent.read_bytes()
        intent = json.loads(raw_bytes)
        parse_intent(intent)
        require(raw_bytes == canonical(intent), "INTENT_NOT_CANONICAL_BYTES")
        verify_separate_principals(args.owner_signers, args.sirt_signers, intent["reviewer_login"])
        verify_signature(raw_bytes, args.owner_signature, args.owner_signers, "wow-owner")
        verify_signature(raw_bytes, args.sirt_signature, args.sirt_signers,
                         "wow-sirt-" + intent["reviewer_login"])
        # The same credential may be repository owner, but must never also be
        # the registered independent SIRT reviewer.
        try:
            executor_login = json.loads(run("gh", "api", "user")).get("login", "")
        except (ValueError, TypeError, AttributeError) as exc:
            raise RecoveryHold("ADMIN_IDENTITY_UNREADABLE") from exc
        require(executor_login and executor_login.lower() != intent["reviewer_login"].lower(),
                "SIRT_REVIEWER_CANNOT_BE_ADMIN_EXECUTOR")
        require(args.exclusive_window_verified, "EXCLUSIVE_MERGE_WINDOW_ATTESTATION_REQUIRED")
        # Flags represent OPERATOR attestations only, not automated evidence.
        # The independent witness must have a DIFFERENT administrator credential
        # and a copy of the exact baseline snapshot before the operator proceeds.
        def verify_witness():
            verify_witness_ready(intent, args.witness_ready, args.witness_ready_signature,
                                 args.sirt_signers, executor_login)
        execute(api, intent, args.checkpoint, verify_witness=verify_witness)


if __name__ == "__main__":
    try:
        main()
    except (RecoveryHold, OSError, ValueError, KeyError, TypeError) as exc:
        print("BREAKGLASS_HOLD:" + str(exc), file=sys.stderr)
        sys.exit(2)
