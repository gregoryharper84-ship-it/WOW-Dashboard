"""#1739: fail-closed database-backed incident writer leases (no external dependencies)."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


class LeaseError(RuntimeError):
    pass


def rpc(name: str, payload: dict):
    endpoint = os.environ.get("SUPABASE_URL", "").rstrip("/")
    token = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not endpoint.startswith("https://") or not token:
        raise LeaseError("LEASE_STORE_UNAVAILABLE:credentials")
    request = Request(
        endpoint + "/rest/v1/rpc/wow_engineering_" + name + "_writer_lease",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "apikey": token,
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=12) as reply:
            return json.load(reply)
    except (HTTPError, URLError, ValueError, TimeoutError) as err:
        # Errors must never include secret-bearing request headers or payload.
        raise LeaseError("LEASE_STORE_UNAVAILABLE:" + type(err).__name__) from None


def _load(path: Path) -> list[dict]:
    try:
        leases = json.loads(path.read_text())
        assert isinstance(leases, list) and leases
        for item in leases:
            assert isinstance(item["incident_id"], int) and item["incident_id"] > 0
            assert isinstance(item["epoch"], int) and item["epoch"] > 0
            assert isinstance(item["owner_id"], str) and len(item["owner_id"]) >= 8
        return leases
    except (OSError, ValueError, AssertionError, KeyError, TypeError):
        raise LeaseError("BAD_LEASE_RECEIPT") from None


def claim(ids: list[int], owner: str, group: str, path: Path):
    if not ids or len(set(ids)) != len(ids) or not owner.startswith("github-actions:"):
        raise LeaseError("BAD_PROVENANCE")
    if path.exists():
        raise LeaseError("LEASE_RECEIPT_ALREADY_EXISTS")
    held = []
    try:
        for incident_id in ids:
            result = rpc("claim", {
                "p_incident_id": incident_id,
                "p_owner_id": owner,
                "p_lease_group": group,
                "p_branch": "",
                "p_pr_number": None,
                "p_ttl_seconds": 1800,
            })
            if not isinstance(result, dict) or result.get("ok") is not True:
                raise LeaseError("LEASE_BUSY:" + str(incident_id))
            if result.get("owner_id") != owner or result.get("incident_id") != incident_id:
                raise LeaseError("BAD_LEASE_RESPONSE")
            epoch = result.get("epoch")
            if type(epoch) is not int or epoch <= 0:
                raise LeaseError("BAD_LEASE_RESPONSE")
            held.append({"incident_id": incident_id, "owner_id": owner, "epoch": epoch})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(held, sort_keys=True) + "\n")
        os.chmod(path, 0o600)
    except Exception:
        for lease in reversed(held):
            try:
                rpc("release", { "p_incident_id": lease["incident_id"],
                    "p_owner_id": owner, "p_epoch": lease["epoch"] })
            except LeaseError:
                pass
        raise


def gate(path: Path):
    for lease in _load(path):
        result = rpc("renew", {
            "p_incident_id": lease["incident_id"], "p_owner_id": lease["owner_id"],
            "p_epoch": lease["epoch"], "p_ttl_seconds": 1800,
        })
        if result is not True:
            raise LeaseError("LEASE_LOST:" + str(lease["incident_id"]))


def heartbeat(path: Path, interval: int = 300):
    """Renew periodically. Exit nonzero on lost ownership; mutation gates still fence."""
    if interval < 60 or interval > 600:
        raise LeaseError("INVALID_RENEW_INTERVAL")
    while True:
        gate(path)
        time.sleep(interval)


def release(path: Path):
    if not path.exists():
        return
    for lease in _load(path):
        result = rpc("release", {
            "p_incident_id": lease["incident_id"],
            "p_owner_id": lease["owner_id"], "p_epoch": lease["epoch"],
        })
        if result is not True:
            raise LeaseError("LEASE_RELEASE_REJECTED:" + str(lease["incident_id"]))
    path.unlink()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["claim", "gate", "release", "heartbeat"])
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--incidents", default="")
    parser.add_argument("--owner", default="")
    parser.add_argument("--group", default="GLOBAL")
    args = parser.parse_args()
    try:
        if args.action == "claim":
            ids = [int(i) for i in args.incidents.split(",") if i and i != "null" and i != "NONE"]
            claim(ids, args.owner, args.group, args.receipt)
        elif args.action == "gate":
            gate(args.receipt)
        elif args.action == "heartbeat":
            heartbeat(args.receipt)
        else:
            release(args.receipt)
    except (LeaseError, ValueError) as error:
        print("::error::" + str(error))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
