#!/usr/bin/env python3
"""Close stale `probe:` PRs and mark their branches for deletion (#1550/#1663 item 5).

Pure decision logic: no network I/O, no wall-clock reads. The caller supplies the
current open-PR list and the current time; this module decides which PRs are eligible
for closure. A PR is eligible when it is open, labelled or titled `probe:`, and older
than the age threshold (24h by default). can_execute=false; this module only selects,
it never calls the GitHub API itself.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Any

PROBE_PREFIX = "probe:"
DEFAULT_MAX_AGE_HOURS = 24


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _is_probe_pr(pr: dict[str, Any]) -> bool:
    title = pr.get("title") or ""
    if title.startswith(PROBE_PREFIX):
        return True
    for label in pr.get("labels") or []:
        name = label.get("name") if isinstance(label, dict) else label
        if isinstance(name, str) and name.startswith(PROBE_PREFIX):
            return True
    return False


def select_probe_prs_to_close(
    prs: list[dict[str, Any]],
    *,
    now: datetime,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
) -> list[dict[str, Any]]:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    selected: list[dict[str, Any]] = []
    for pr in prs:
        if pr.get("state") != "open":
            continue
        if not _is_probe_pr(pr):
            continue
        created_at_raw = pr.get("created_at")
        if not created_at_raw:
            continue
        created_at = _parse_timestamp(created_at_raw)
        age_hours = (now - created_at).total_seconds() / 3600
        if age_hours < max_age_hours:
            continue
        selected.append(
            {
                "number": pr.get("number"),
                "branch": (pr.get("head") or {}).get("ref"),
                "age_hours": round(age_hours, 2),
            }
        )
    return selected


def _cmd_plan(args: argparse.Namespace) -> int:
    with open(args.prs_json, "r", encoding="utf-8") as fh:
        prs = json.load(fh)
    now = _parse_timestamp(args.now) if args.now else datetime.now(timezone.utc)
    plan = select_probe_prs_to_close(prs, now=now, max_age_hours=args.max_age_hours)
    print(json.dumps(plan))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prs-json", required=True)
    parser.add_argument("--now", default=None, help="ISO8601 timestamp; defaults to current UTC time")
    parser.add_argument("--max-age-hours", type=float, default=DEFAULT_MAX_AGE_HOURS)
    parser.set_defaults(func=_cmd_plan)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
