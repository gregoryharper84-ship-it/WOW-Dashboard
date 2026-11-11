"""Protected Engineering App entrypoint for a single fenced GitHub mutation.

This is a staged integration adapter, NOT an independent writer/controller.
The existing Engineering worker must first produce an authoritative lease
receipt and only then invoke this module with App-only credentials.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

try:
    from .engineering_fenced_writer import (
        REPO, FencingHold, Gateway, GitHubAppWriter, Intent, SupabaseRpcStore,
    )
except ImportError:  # trusted direct-script Actions invocation
    from engineering_fenced_writer import (
        REPO, FencingHold, Gateway, GitHubAppWriter, Intent, SupabaseRpcStore,
    )


def receipt_identity(path: Path, env: dict) -> tuple[int, str, int, str]:
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(items, list) or len(items) != 1:
            raise ValueError("one canonical lease receipt required")
        row = items[0]
        incident = row["incident_id"]
        owner = row["owner_id"]
        epoch = row["epoch"]
        group = env["WOW_ENGINEERING_LEASE_GROUP"]
        run = env["GITHUB_RUN_ID"]
        attempt = env["GITHUB_RUN_ATTEMPT"]
        if (type(incident) is not int or incident <= 0 or
                type(epoch) is not int or epoch <= 0 or
                not isinstance(owner, str) or
                not owner.startswith(f"github-actions:{run}:{attempt}:") or
                len(owner) > 256 or
                not isinstance(group, str) or not group):
            raise ValueError("untrusted actor or lease identity")
        return incident, owner, epoch, group
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise FencingHold("WRITER_LEASE_RECEIPT_UNVERIFIED") from None


def deliver(args, env: dict | None = None, *, store=None, github=None) -> str:
    env = dict(os.environ if env is None else env)
    # This can never be invoked as provider-supplied code or on a PR checkout.
    if (env.get("GITHUB_REPOSITORY") != REPO
            or env.get("GITHUB_REF") != "refs/heads/main"
            or env.get("GITHUB_WORKFLOW") != "wow-v17-engineering-auto-repair"
            or env.get("WOW_CAN_EXECUTE") != "false"
            or env.get("WOW_DRY_RUN_ONLY") != "true"):
        raise FencingHold("UNTRUSTED_ENGINEERING_WORKFLOW_CONTEXT")
    app = env.get("APP_TOKEN", "")
    readonly = env.get("GH_TOKEN", "")
    if not app or not readonly or app == readonly:
        raise FencingHold("INDEPENDENT_APP_WRITE_IDENTITY_REQUIRED")
    incident, owner, epoch, group = receipt_identity(Path(args.receipt), env)
    if args.operation == "CLOSE_EPOCH":
        if any(getattr(args, field, "") for field in (
                "mutation_id", "branch", "sha", "payload_file", "source_pr", "source_sha")):
            raise FencingHold("EPOCH_CLOSE_MUTATION_ARGUMENTS_FORBIDDEN")
        if store is None:
            store = SupabaseRpcStore(env.get("SUPABASE_URL", ""),
                                    env.get("SUPABASE_SERVICE_ROLE_KEY", ""),
                                    env.get("WOW_ENGINEERING_SUPABASE_PROJECT_ID", ""))
        status = store.close_epoch(incident, owner, epoch, group)
        if status not in {"DRAINED", "DRAINING"}:
            raise FencingHold("WRITER_EPOCH_CLOSE_DENIED")
        return status
    payload = ""
    if args.operation == "CREATE_PR":
        try:
            payload = Path(args.payload_file).read_text(encoding="utf-8")
        except (OSError, TypeError):
            raise FencingHold("PR_BODY_UNAVAILABLE") from None
    elif args.payload_file:
        raise FencingHold("NON_PR_BODY_FORBIDDEN")
    source_pr = getattr(args, "source_pr", 0) or None
    source_sha = getattr(args, "source_sha", "")
    if (source_pr is None) != (not bool(source_sha)):
        raise FencingHold("SOURCE_REPLAY_IDENTITY_INCOMPLETE")
    intent = Intent(args.mutation_id, incident, owner, epoch, group,
                    args.operation, args.branch, args.sha, payload,
                    source_pr=source_pr, source_sha=source_sha,
                    draft=(source_pr is None))
    intent.validate()
    if args.operation == "PUSH_REF" and github is None:
        # A deterministic local checkout failure must never create a durable
        # PENDING remote reservation; record it before reserving in the DB.
        import subprocess
        try:
            sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
            remote = subprocess.check_output(["git", "remote", "get-url", "origin"], text=True).strip()
        except (OSError, subprocess.CalledProcessError):
            raise FencingHold("PUSH_REF_LOCAL_PREFLIGHT_FAILED") from None
        if sha != args.sha or remote not in {
                f"https://github.com/{REPO}", f"https://github.com/{REPO}.git"}:
            raise FencingHold("PUSH_REF_LOCAL_PREFLIGHT_FAILED")
    if store is None:
        store = SupabaseRpcStore(env.get("SUPABASE_URL", ""),
                                env.get("SUPABASE_SERVICE_ROLE_KEY", ""),
                                env.get("WOW_ENGINEERING_SUPABASE_PROJECT_ID", ""))
    if github is None:
        github = GitHubAppWriter(app)
    return Gateway(store, github).deliver(intent)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--receipt", required=True)
    p.add_argument("--mutation-id", default="")
    p.add_argument("--operation", required=True, choices=["CREATE_BRANCH","PUSH_REF","CREATE_PR","CLOSE_EPOCH"])
    p.add_argument("--branch", default="")
    p.add_argument("--sha", default="")
    p.add_argument("--payload-file", default="")
    p.add_argument("--source-pr", type=int, default=0)
    p.add_argument("--source-sha", default="")
    args = p.parse_args()
    try:
        status = deliver(args)
    except FencingHold as err:
        print(f"::error::{err}")
        raise SystemExit(1) from None
    print(json.dumps({"status":status, "can_execute":False}, sort_keys=True))
    if status == "DRAINING":
        raise SystemExit(1)  # Durable owned re-entry; never a release authorization.


if __name__ == "__main__":
    main()
