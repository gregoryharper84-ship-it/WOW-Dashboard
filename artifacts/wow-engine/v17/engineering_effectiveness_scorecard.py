"""Engineering effectiveness scorecard for WOW V17.

The scorecard measures engineering process outcomes only. It does not evaluate,
change, or rank sporting probabilities.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

FOLLOWUP_PATTERNS = (
    re.compile(r"(?im)^\s*Followup-Of:\s*#?\d+\s*$"),
    re.compile(r"(?i)\bfollow[- ]?up\s+(?:to|for)\s+#\d+"),
    re.compile(r"(?i)\breplacement\s+for\s+.*#\d+"),
    re.compile(r"(?i)\bsupersedes?\s+#\d+"),
)
SUPERSEDED_BY = re.compile(r"(?im)^\s*Superseded-By:\s*#?(\d+)\s*$")


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def is_followup(pr: dict[str, Any]) -> bool:
    text = f"{pr.get('title') or ''}\n{pr.get('body') or ''}"
    return any(pattern.search(text) for pattern in FOLLOWUP_PATTERNS)


def superseded_by(pr: dict[str, Any]) -> int | None:
    match = SUPERSEDED_BY.search(str(pr.get("body") or ""))
    return int(match.group(1)) if match else None


def summarize(
    merged_prs: list[dict[str, Any]],
    open_prs: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    window_hours: int = 72,
    stale_hours: int = 72,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    window_start = now - timedelta(hours=window_hours)
    stale_before = now - timedelta(hours=stale_hours)

    recent = [
        pr
        for pr in merged_prs
        if (merged_at := _dt(pr.get("merged_at"))) is not None and merged_at >= window_start
    ]
    followups = [pr for pr in recent if is_followup(pr)]
    explicit_superseded = [pr for pr in open_prs if superseded_by(pr) is not None]
    stale_open = [
        pr
        for pr in open_prs
        if (created_at := _dt(pr.get("created_at"))) is not None and created_at <= stale_before
    ]

    merged_n = len(recent)
    followup_n = len(followups)
    first_pass_proxy = None if merged_n == 0 else max(0.0, (merged_n - followup_n) / merged_n)

    return {
        "schema_version": "WOW_V17_ENGINEERING_EFFECTIVENESS_SCORECARD_V1",
        "window_hours": window_hours,
        "merged_prs": merged_n,
        "followup_or_replacement_prs": followup_n,
        "first_pass_proxy": first_pass_proxy,
        "explicit_superseded_open_prs": [int(pr["number"]) for pr in explicit_superseded],
        "stale_open_prs": [int(pr["number"]) for pr in stale_open],
        "followup_prs": [int(pr["number"]) for pr in followups],
        "metric_note": "first_pass_proxy is a conservative process proxy based on explicit/recognizable linked follow-up metadata; product acceptance remains separately authoritative",
        "can_execute": False,
    }


def _load(path: str) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text())
    if not isinstance(payload, list):
        raise ValueError(f"expected list in {path}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--merged-json", required=True)
    parser.add_argument("--open-json", required=True)
    parser.add_argument("--window-hours", type=int, default=72)
    parser.add_argument("--stale-hours", type=int, default=72)
    args = parser.parse_args()
    result = summarize(
        _load(args.merged_json),
        _load(args.open_json),
        window_hours=args.window_hours,
        stale_hours=args.stale_hours,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
