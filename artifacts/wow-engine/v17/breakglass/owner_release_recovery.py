#!/usr/bin/env python3
"""WOW V17 owner-signed emergency Release compatibility recovery.

No second human or SIRT signature is required. This is an explicit owner-only
emergency policy candidate, not a regular merge route or a Release App publisher.
Use only on a trusted administrator machine. Repo-wide temporary rule exposure
must be authorized separately by the offline owner signature.
"""
from __future__ import annotations
import argparse
import binascii
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

from release_breakglass import (
    CANONICAL_REPO, RULESET_ID, PINNED_OWNER_KEY_FINGERPRINT,
    CHECK_CONTEXTS, GitHub, RecoveryHold, require, run, canonical, sha256,
    snapshot, checks_from_ruleset, expected_check_set, temporary_ruleset,
    verify_latest_app_checks, restore, save_private,
)

OWNER = "gregoryharper84-ship-it"
MODE = "WOW_OWNER_SIGNED_BREAKGLASS_V1"
RISK = "OWNER_AUTHORIZES_REPOSITORY_WIDE_TEMPORARY_RELEASE_EXCEPTION"
SHA = re.compile(r"^[0-9a-f]{40}$")
ALLOWED_PREFIXES = (".github/workflows/", ".github/scripts/tests/")

def parse_intent(raw, *, allow_expired=False, now=None):
    keys = {"schema", "repo", "ruleset_id", "pr", "head_sha", "main_sha",
            "incident", "allowed_files", "baseline_ruleset_sha256",
            "expires_utc", "window_seconds", "removed_check", "owner_risk_acceptance"}
    require(isinstance(raw, dict) and set(raw) == keys, "OWNER_INTENT_SCHEMA_INVALID")
    require(raw["schema"] == MODE and raw["repo"] == CANONICAL_REPO, "OWNER_INTENT_SCOPE_INVALID")
    require(type(raw["ruleset_id"]) is int and raw["ruleset_id"] == RULESET_ID, "RULESET_NOT_PINNED")
    require(type(raw["pr"]) is int and raw["pr"] > 0, "PR_INVALID")
    require(type(raw["incident"]) is int and raw["incident"] > 0, "INCIDENT_INVALID")
    require(all(isinstance(raw[k], str) and SHA.fullmatch(raw[k]) for k in ("head_sha", "main_sha")),
            "INTENT_SHA_INVALID")
    require(raw["owner_risk_acceptance"] == RISK, "REPO_WIDE_RISK_NOT_SIGNED")
    require(raw["removed_check"] == list(CHECK_CONTEXTS[-1]), "ONLY_RELEASE_CHECK_MAY_BE_REMOVED")
    require(type(raw["window_seconds"]) is int and 15 <= raw["window_seconds"] <= 120,
            "WINDOW_INVALID")
    files = raw["allowed_files"]
    require(isinstance(files, list) and 1 <= len(files) <= 8 and len(set(files)) == len(files),
            "FILE_ALLOWLIST_INVALID")
    require(all(isinstance(x, str) and x.startswith(ALLOWED_PREFIXES)
                and ".." not in Path(x).parts for x in files), "FILE_SCOPE_INVALID")
    require(isinstance(raw["baseline_ruleset_sha256"], str) and
            re.fullmatch(r"[0-9a-f]{64}", raw["baseline_ruleset_sha256"]), "BASELINE_DIGEST_INVALID")
    try:
        expires = dt.datetime.fromisoformat(raw["expires_utc"].replace("Z", "+00:00"))
    except (KeyError, AttributeError, ValueError) as exc:
        raise RecoveryHold("INTENT_EXPIRY_INVALID") from exc
    clock = now or dt.datetime.now(dt.timezone.utc)
    require(expires.utcoffset() == dt.timedelta(0), "EXPIRY_NOT_UTC")
    if not allow_expired:
        require(0 < (expires - clock).total_seconds() <= 3600, "INTENT_EXPIRED")
    return raw

def owner_signer_file(path: Path) -> None:
    require(path.is_file(), "OWNER_SIGNER_FILE_MISSING")
    lines = [x for x in path.read_text().splitlines()
             if x.startswith("wow-owner ") and 'namespaces="wow-breakglass"' in x]
    require(len(lines) == 1, "OWNER_SIGNER_AMBIGUOUS")
    fields = lines[0].split()
    try:
        idx = next(i for i, v in enumerate(fields) if v == "ssh-ed25519")
        key = fields[idx + 1]
        import base64
        digest = base64.b64encode(hashlib.sha256(base64.b64decode(key, validate=True)).digest()).decode().rstrip("=")
    except (ValueError, IndexError, StopIteration, binascii.Error) as exc:
        raise RecoveryHold("OWNER_SIGNER_KEY_INVALID") from exc
    require("SHA256:" + digest == PINNED_OWNER_KEY_FINGERPRINT, "OWNER_FINGERPRINT_MISMATCH")

def verify_owner_signature(raw: bytes, signature: Path, signers: Path) -> None:
    owner_signer_file(signers)
    require(signature.is_file(), "OWNER_SIGNATURE_MISSING")
    run("ssh-keygen", "-Y", "verify", "-f", str(signers), "-I", "wow-owner",
        "-n", "wow-breakglass", "-s", str(signature), input_bytes=raw)

def preflight(api: GitHub, intent: dict) -> dict:
    pr = api.get(f"pulls/{intent['pr']}")
    require(pr.get("state") == "open" and pr.get("draft") is False and not pr.get("merged"),
            "PR_NOT_READY")
    require((pr.get("head") or {}).get("sha") == intent["head_sha"]
            and ((pr.get("head") or {}).get("repo") or {}).get("full_name") == CANONICAL_REPO,
            "PR_HEAD_MOVED_OR_FOREIGN")
    require((pr.get("base") or {}).get("ref") == "main", "PR_BASE_NOT_MAIN")
    require(api.get("git/ref/heads/main")["object"]["sha"] == intent["main_sha"], "MAIN_MOVED")
    rs = snapshot(api.get(f"rulesets/{RULESET_ID}"))
    require(sha256(rs) == intent["baseline_ruleset_sha256"]
            and checks_from_ruleset(rs) == expected_check_set(), "RULESET_CHANGED")
    paths = []
    files = api.pages(f"pulls/{intent['pr']}/files")
    require(len(files) == pr.get("changed_files"), "FILES_INCOMPLETE")
    for f in files:
        require(isinstance(f, dict) and isinstance(f.get("filename"), str), "FILE_INVALID")
        paths.append(f["filename"])
        if f.get("status") == "renamed":
            require(isinstance(f.get("previous_filename"), str), "RENAME_ORIGIN_MISSING")
            paths.append(f["previous_filename"])
    require(set(paths) == set(intent["allowed_files"]), "FILE_SCOPE_CHANGED")
    verify_latest_app_checks(api.pages(f"commits/{intent['head_sha']}/check-runs", "check_runs"),
                             intent["head_sha"])
    reviews = api.pages(f"pulls/{intent['pr']}/reviews")
    approvals = [r for r in reviews if (r.get("user") or {}).get("login") == OWNER]
    require(approvals and all(type(r.get("id")) is int for r in approvals), "OWNER_REVIEW_MISSING")
    last = max(approvals, key=lambda x: x["id"])
    require(last.get("state") == "APPROVED" and last.get("commit_id") == intent["head_sha"],
            "OWNER_EXACT_HEAD_APPROVAL_MISSING")
    # A failed SIRT-labeled owner review is not independent authorization. No
    # SIRT decision is consumed by the owner-only alternative policy.
    require(not [x for x in api.pages("pulls?state=open") if x.get("auto_merge") is not None],
            "AUTO_MERGE_ARMED")
    return rs

def authenticate_admin(api: GitHub) -> None:
    require(json.loads(run("gh", "api", "user")).get("login") == OWNER,
            "OWNER_ACCOUNT_REQUIRED")
    repo = json.loads(run("gh", "api", f"repos/{CANONICAL_REPO}"))
    require((repo.get("permissions") or {}).get("admin") is True, "ADMIN_PERMISSION_REQUIRED")

def write_intent(api: GitHub, args) -> None:
    p = api.get(f"pulls/{args.pr}")
    rs = snapshot(api.get(f"rulesets/{RULESET_ID}"))
    require(checks_from_ruleset(rs) == expected_check_set(), "BASELINE_NOT_FIVE_CHECKS")
    intent = dict(schema=MODE, repo=CANONICAL_REPO, ruleset_id=RULESET_ID,
                  pr=args.pr, head_sha=p["head"]["sha"],
                  main_sha=api.get("git/ref/heads/main")["object"]["sha"],
                  incident=args.incident, allowed_files=sorted(args.allowed_file),
                  baseline_ruleset_sha256=sha256(rs),
                  expires_utc=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=30)).isoformat().replace("+00:00","Z"),
                  window_seconds=90, removed_check=list(CHECK_CONTEXTS[-1]),
                  owner_risk_acceptance=RISK)
    parse_intent(intent)
    require(not args.output.exists(), "INTENT_EXISTS")
    baseline = args.output.with_suffix(".baseline.json")
    require(not baseline.exists(), "BASELINE_EXISTS")
    # Check the exact PR/CI before issuing a signable intent.
    preflight(api, intent)
    save_private(baseline, canonical(rs))
    save_private(args.output, canonical(intent))
    print("OWNER_INTENT_PREPARED_NO_GITHUB_MUTATION", args.output)
    print("BASELINE", baseline)
    print("BASELINE_SHA256", sha256(rs))

def watchdog_ready_healthy(ready: Path, proc, expected_digest: str, *, min_seconds: int = 45) -> None:
    require(proc.poll() is None and ready.is_file(), "WATCHDOG_PROCESS_NOT_ALIVE")
    obj = json.loads(ready.read_bytes())
    require(isinstance(obj, dict) and set(obj) ==
            {"pid", "intent_digest", "expires_monotonic"}, "WATCHDOG_READY_SCHEMA_INVALID")
    require(obj["pid"] == proc.pid and obj["intent_digest"] == expected_digest,
            "WATCHDOG_READY_MISMATCH")
    deadline = obj["expires_monotonic"]
    require(type(deadline) in (int, float) and deadline - time.monotonic() >= min_seconds,
            "WATCHDOG_ROLLBACK_DEADLINE_TOO_NEAR")

def _watchdog_process(intent: Path, baseline: Path, ready: Path, log: Path):
    cmd = [sys.executable, str(Path(__file__).with_name("owner_release_watchdog.py")),
           "--intent", str(intent), "--baseline", str(baseline), "--ready", str(ready)]
    with log.open("ab", buffering=0) as out:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out,
                                stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(25):
        if proc.poll() is not None:
            raise RecoveryHold("WATCHDOG_EXITED_BEFORE_READY")
        if ready.exists():
            watchdog_ready_healthy(ready, proc, hashlib.sha256(intent.read_bytes()).hexdigest())
            return proc
        time.sleep(0.2)
    raise RecoveryHold("WATCHDOG_READY_TIMEOUT")

def execute(api: GitHub, intent: dict, intent_path: Path, checkpoint: Path) -> None:
    authenticate_admin(api)
    baseline = preflight(api, intent)
    require(not checkpoint.exists(), "CHECKPOINT_EXISTS")
    checkpoint.mkdir(mode=0o700, parents=True)
    baseline_path = checkpoint / "baseline.json"
    intent_copy = checkpoint / "intent.json"
    save_private(baseline_path, canonical(baseline))
    save_private(intent_copy, canonical(intent))
    save_private(checkpoint/"baseline.sha256", (sha256(baseline)+"\n").encode())
    # Recheck all evidence before starting a separately surviving process.
    require(sha256(preflight(api, intent)) == sha256(baseline), "PREFLIGHT_CHANGED")
    watchdog = _watchdog_process(intent_copy, baseline_path, checkpoint/"watchdog.ready",
                                checkpoint/"watchdog.log")
    require(watchdog.poll() is None, "WATCHDOG_NOT_ALIVE")
    old = {}
    def interrupted(signum, frame):
        raise RecoveryHold("OPERATOR_INTERRUPTED")
    for s in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):
        old[s] = signal.signal(s, interrupted)
    started = False
    error = None
    try:
        require(watchdog.poll() is None, "WATCHDOG_DIED_BEFORE_MUTATION")
        require(sha256(preflight(api, intent)) == sha256(baseline), "PREWRITE_DRIFT")
        require(watchdog.poll() is None, "WATCHDOG_DIED_BEFORE_MUTATION")
        parse_intent(intent)  # reject expiry while preflight/witness was starting
        watchdog_ready_healthy(checkpoint/"watchdog.ready", watchdog,
                               hashlib.sha256(intent_copy.read_bytes()).hexdigest())
        started = True
        temporary = temporary_ruleset(baseline)
        api.put(f"rulesets/{RULESET_ID}", temporary)
        require(sha256(snapshot(api.get(f"rulesets/{RULESET_ID}"))) == sha256(temporary),
                "TEMP_RULESET_READBACK_FAILED")
        require(api.get("git/ref/heads/main")["object"]["sha"] == intent["main_sha"],
                "MAIN_CHANGED_IN_WINDOW")
        require(api.get(f"pulls/{intent['pr']}")["head"]["sha"] == intent["head_sha"],
                "PR_CHANGED_IN_WINDOW")
        outcome = api.merge(intent["pr"], intent["head_sha"])
        require(outcome.get("merged") is True, "MERGE_UNCONFIRMED")
        save_private(checkpoint/"merge-receipt.json", canonical(outcome))
    except (RecoveryHold, OSError, ValueError, KeyError, TypeError) as exc:
        error = str(exc)
    finally:
        try:
            if started:
                restore(api, baseline)
        finally:
            for s, handler in old.items():
                signal.signal(s, handler)
    require(error is None, "MERGE_FAILED_OR_UNCONFIRMED:"+str(error))
    require(api.get(f"pulls/{intent['pr']}").get("merged") is True, "PR_MERGE_READBACK_FAILED")
    print("OWNER_EXACT_HEAD_MERGED_FIVE_CHECKS_RESTORED")
    print("NEW_PROTECTED_MAIN_QA_RELEASE_VERIFICATION_REQUIRED")

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="mode", required=True)
    a = sub.add_parser("inspect")
    a.add_argument("--pr", type=int, required=True)
    a = sub.add_parser("prepare")
    a.add_argument("--pr", type=int, required=True)
    a.add_argument("--incident", type=int, required=True)
    a.add_argument("--allowed-file", action="append", required=True)
    a.add_argument("--output", type=Path, required=True)
    a = sub.add_parser("execute")
    a.add_argument("--intent", type=Path, required=True)
    a.add_argument("--owner-signature", type=Path, required=True)
    a.add_argument("--owner-signers", type=Path, required=True)
    a.add_argument("--checkpoint", type=Path, required=True)
    a.add_argument("--exclusive-window-verified", action="store_true")
    a = sub.add_parser("restore")
    a.add_argument("--intent", type=Path, required=True)
    a.add_argument("--owner-signature", type=Path, required=True)
    a.add_argument("--owner-signers", type=Path, required=True)
    a.add_argument("--baseline", type=Path, required=True)
    args = ap.parse_args()
    api = GitHub()
    if args.mode == "inspect":
        p = api.get(f"pulls/{args.pr}")
        rs = snapshot(api.get(f"rulesets/{RULESET_ID}"))
        print(json.dumps({"pr":args.pr,"head":p["head"]["sha"],
            "main":api.get("git/ref/heads/main")["object"]["sha"],
            "ruleset_digest":sha256(rs),"five_checks":checks_from_ruleset(rs)==expected_check_set()},indent=2))
        return
    if args.mode == "prepare":
        authenticate_admin(api)
        write_intent(api,args)
        return
    raw = args.intent.read_bytes()
    intent = parse_intent(json.loads(raw), allow_expired=args.mode=="restore")
    require(raw == canonical(intent), "INTENT_BYTES_NOT_CANONICAL")
    verify_owner_signature(raw,args.owner_signature,args.owner_signers)
    authenticate_admin(api)
    if args.mode == "restore":
        baseline = json.loads(args.baseline.read_bytes())
        require(sha256(baseline) == intent["baseline_ruleset_sha256"],
                "RESTORE_SNAPSHOT_NOT_SIGNED")
        restore(api,baseline)
        return
    require(args.exclusive_window_verified, "REPOSITORY_WIDE_EXCLUSIVITY_UNATTESTED")
    execute(api,intent,args.intent,args.checkpoint)

if __name__ == "__main__":
    try:
        main()
    except (RecoveryHold,OSError,ValueError,TypeError,KeyError) as exc:
        print("OWNER_BREAKGLASS_HOLD:"+str(exc),file=sys.stderr)
        raise SystemExit(2)
