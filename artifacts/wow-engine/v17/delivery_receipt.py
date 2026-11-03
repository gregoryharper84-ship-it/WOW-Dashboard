"""Read-only WOW V17 delivery evidence collector.

Separate MLB and NFL event ledgers. Never interpret persisted numerical model
outputs as final publication authority, certification, or execution.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

CAN_EXECUTE = False
EXPECTED_PROJECT_REF = "iczfhsmjrrafhvcpmqhr"
REQUIRED_CONFIG = (
    "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "RENDER_API_KEY",
    "RENDER_SERVICE_ID", "WOW_DELIVERY_EXPECTED_SHA",
)
_ACTIVITY_TIME = {
    "wow_predictions": "created_at",
    "wow_event_predictions": "created_at",
    "wow_nfl_event_predictions": "created_at",
    "wow_action_invocation_receipts": "occurred_at",
}


class DeliveryError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def utc(value: datetime | None = None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None:
        raise DeliveryError("DELIVERY_CLOCK_INVALID")
    return result.astimezone(timezone.utc)


def stamp(value: datetime) -> str:
    return utc(value).isoformat().replace("+00:00", "Z")


def bounded_int(raw: Any, *, minimum: int, maximum: int) -> int:
    try:
        number = int(str(raw))
    except (TypeError, ValueError) as exc:
        raise DeliveryError("DELIVERY_WINDOW_INVALID") from exc
    if not minimum <= number <= maximum:
        raise DeliveryError("DELIVERY_WINDOW_INVALID")
    return number


def supabase_ref(url: str) -> str:
    try:
        parsed = urlparse(url)
        valid_url = parsed.scheme == "https" and not (
            parsed.username or parsed.password or parsed.port
        )
    except ValueError as exc:
        raise DeliveryError("DELIVERY_SUPABASE_TARGET_INVALID") from exc
    match = re.fullmatch(r"([a-z0-9]{20})\\.supabase\\.co", parsed.hostname or "")
    if not valid_url or not match or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise DeliveryError("DELIVERY_SUPABASE_TARGET_INVALID")
    return match.group(1)


def _request_json(url: str, headers: dict[str, str]) -> tuple[Any, dict[str, str]]:
    try:
        with urlopen(Request(url, headers=headers, method="GET"), timeout=20) as response:
            raw = response.read(2_000_000)
            return json.loads(raw), {str(k).lower(): str(v) for k, v in response.headers.items()}
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        raise DeliveryError("DELIVERY_SOURCE_READ_FAILED") from exc


def _count_from_header(headers: dict[str, str]) -> int:
    match = re.fullmatch(r"(?:\\d+-\\d+|\\*)/(\\d+)", headers.get("content-range", ""))
    if not match:
        raise DeliveryError("DELIVERY_COUNT_NOT_PROVEN")
    return int(match.group(1))


class SupabaseReadOnly:
    def __init__(self, url: str, key: str):
        self.base = url.rstrip("/")
        self.headers = {
            "apikey": key, "Authorization": "Bearer " + key,
            "Accept": "application/json", "Prefer": "count=exact",
        }

    def rows(self, table: str, select: str, **filters: str) -> tuple[list[dict[str, Any]], int]:
        if not re.fullmatch(r"wow_[a-z0-9_]+", table):
            raise DeliveryError("DELIVERY_TABLE_INVALID")
        query = urlencode({"select": select, "limit": 1, **filters}, safe="(),.*:-+")
        raw, headers = _request_json(f"{self.base}/rest/v1/{table}?{query}", self.headers)
        if not isinstance(raw, list) or any(not isinstance(row, dict) for row in raw):
            raise DeliveryError("DELIVERY_SOURCE_SHAPE_INVALID")
        return raw, _count_from_header(headers)

    def count(self, table: str, **filters: str) -> int:
        _, count = self.rows(table, "*", **filters)
        return count


def _render_deploy_sha(service_id: str, key: str) -> str:
    if not re.fullmatch(r"srv-[a-z0-9]+", service_id):
        raise DeliveryError("DELIVERY_RENDER_SERVICE_ID_INVALID")
    data, _ = _request_json(
        "https://api.render.com/v1/services/" + service_id + "/deploys?limit=1",
        {"Authorization": "Bearer " + key, "Accept": "application/json"},
    )
    deploy = data[0].get("deploy") if isinstance(data, list) and data and isinstance(data[0], dict) else None
    if not isinstance(deploy, dict) or deploy.get("status") != "live":
        raise DeliveryError("DELIVERY_RENDER_DEPLOY_UNVERIFIED")
    commit = deploy.get("commit")
    sha = commit.get("id") if isinstance(commit, dict) else None
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        raise DeliveryError("DELIVERY_RENDER_SHA_UNVERIFIED")
    return sha.lower()


def _render_supabase_ref(service_id: str, key: str) -> str:
    data, _ = _request_json(
        "https://api.render.com/v1/services/" + service_id + "/env-vars",
        {"Authorization": "Bearer " + key, "Accept": "application/json"},
    )
    if not isinstance(data, list):
        raise DeliveryError("DELIVERY_RENDER_ENV_UNVERIFIED")
    for item in data:
        var = item.get("envVar") if isinstance(item, dict) else None
        if isinstance(var, dict) and var.get("key") == "SUPABASE_URL":
            value = var.get("value")
            if not isinstance(value, str):
                raise DeliveryError("DELIVERY_RENDER_ENV_UNVERIFIED")
            # A redacted Render value is not a verified project binding.
            return supabase_ref(value)
    raise DeliveryError("DELIVERY_RENDER_ENV_UNVERIFIED")


def _table_window(db: SupabaseReadOnly, name: str, now: datetime, hours: int) -> dict[str, Any]:
    col = _ACTIVITY_TIME[name]
    start = stamp(now - timedelta(hours=hours))
    end = stamp(now + timedelta(minutes=5))
    total = db.count(name)
    recent_including_future = db.count(name, **{col: "gte." + start})
    future = db.count(name, **{col: "gt." + end})
    latest, _ = db.rows(name, col, order=col + ".desc", **{col: "lte." + end})
    return {
        "total": total, "recent": recent_including_future - future,
        "latest_valid": latest[0].get(col) if latest else None,
        "future_excluded": future, "activity_window_hours": hours,
    }


def build_receipt(
    config: dict[str, str], *, at: datetime | None = None,
    db: SupabaseReadOnly | None = None,
    render_deploy: Any = _render_deploy_sha,
    render_env: Any = _render_supabase_ref,
) -> dict[str, Any]:
    missing = sorted(name for name in REQUIRED_CONFIG if not config.get(name))
    if missing:
        return {
            "status": "BLOCKED", "code": "DELIVERY_CONFIGURATION_MISSING",
            "missing_config_names": missing, "can_execute": False,
        }
    now = utc(at)
    hours = bounded_int(config.get("WOW_DELIVERY_ACTIVITY_WINDOW_HOURS", "72"), minimum=1, maximum=720)
    days = bounded_int(config.get("WOW_DELIVERY_CALIBRATION_WINDOW_DAYS", "180"), minimum=30, maximum=730)
    sha = config["WOW_DELIVERY_EXPECTED_SHA"].strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise DeliveryError("DELIVERY_EXPECTED_SHA_INVALID")
    ref = supabase_ref(config["SUPABASE_URL"])
    if ref != config.get("WOW_DELIVERY_EXPECTED_SUPABASE_PROJECT_REF", EXPECTED_PROJECT_REF):
        raise DeliveryError("DELIVERY_SUPABASE_PROJECT_MISMATCH")
    if render_env(config["RENDER_SERVICE_ID"], config["RENDER_API_KEY"]) != ref:
        raise DeliveryError("DELIVERY_RENDER_SUPABASE_TARGET_MISMATCH")
    deployed = render_deploy(config["RENDER_SERVICE_ID"], config["RENDER_API_KEY"])
    if deployed != sha:
        raise DeliveryError("DELIVERY_RENDER_SHA_MISMATCH")
    client = db or SupabaseReadOnly(config["SUPABASE_URL"], config["SUPABASE_SERVICE_ROLE_KEY"])
    lanes = {name: _table_window(client, name, now, hours) for name in _ACTIVITY_TIME}
    fitted_after = stamp(now - timedelta(days=days))
    calibration_total = client.count("wow_calibrators")
    calibration_recent = client.count("wow_calibrators", fitted_at="gte." + fitted_after)
    latest_fit, _ = client.rows("wow_calibrators", "fitted_at,active,promoted", order="fitted_at.desc")
    latest_health, _ = client.rows(
        "wow_nfl_forward_calibration_health",
        "generated_at,graded_n,minimum_forward_required,status,certification_recommendation,blockers",
        order="generated_at.desc",
    )
    future_attempts = client.count(
        "wow_event_source_attempts", attempted_at="gt." + stamp(now + timedelta(minutes=5))
    )
    return {
        "status": "VERIFIED_DELIVERY_EVIDENCE", "code": "DELIVERY_READBACK_COMPLETED",
        "can_execute": False, "generated_at": stamp(now),
        "render": {"service_id": config["RENDER_SERVICE_ID"], "deployed_sha": deployed,
                   "expected_sha": sha, "supabase_project_ref_matches": True},
        "supabase": {
            "project_ref": ref, "activity_window_hours": hours, "lanes": lanes,
            "calibrators": {
                "total": calibration_total, "within_fit_window": calibration_recent,
                "fit_window_days": days,
                "latest_fit": latest_fit[0].get("fitted_at") if latest_fit else None,
            },
            "nfl_forward_health": latest_health[0] if latest_health else None,
            "future_source_attempts_excluded": future_attempts,
        },
        "interpretation": {
            "mlb_event_idle_is_not_global_event_outage": True,
            "nfl_numerical_package_is_not_final_publication": True,
            "nfl_forward_certification_requires_independent_acceptance": True,
            "no_automatic_model_or_artifact_promotion": True,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only V17 delivery evidence")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        receipt = build_receipt(dict(os.environ))
        code = 0 if receipt["status"] == "VERIFIED_DELIVERY_EVIDENCE" else 2
    except DeliveryError as exc:
        receipt, code = {"status": "BLOCKED", "code": exc.code, "can_execute": False}, 2
    Path(args.out).write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": receipt["status"], "code": receipt["code"],
                      "can_execute": False, "receipt_path": args.out}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
