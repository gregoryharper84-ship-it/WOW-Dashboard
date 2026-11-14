#!/usr/bin/env python3
"""Render the daily "WOW autonomy digest" pinned-issue body (#1550/#1663 item 4).

Pure rendering logic: no network I/O. The caller gathers evidence (merged PRs, HOLD
PRs, owner-blocked PRs, stale/conflicted PRs, last main SHA, last Render deployed SHA)
with the read-only GITHUB_TOKEN and hands it here; only the issue write itself uses the
wow-engineering App token. Read-only; never merges, deploys, or grants bypass.
"""
from __future__ import annotations

import argparse
import json
from typing import Any

DIGEST_TITLE = "WOW autonomy digest"


def _pr_lines(prs: list[dict[str, Any]], *, empty_label: str, reason_key: str | None = None) -> list[str]:
    if not prs:
        return [f"- {empty_label}"]
    lines = []
    for pr in prs:
        number = pr.get("number")
        title = pr.get("title", "")
        line = f"- #{number} {title}".rstrip()
        if reason_key and pr.get(reason_key):
            line += f" — `{pr[reason_key]}`"
        lines.append(line)
    return lines


def render_digest(
    *,
    generated_at: str,
    merged_last_24h: list[dict[str, Any]],
    hold_prs: list[dict[str, Any]],
    waiting_on_owner: list[dict[str, Any]],
    stale_or_conflicted: list[dict[str, Any]],
    last_main_sha: str,
    last_render_deployed_sha: str,
) -> str:
    lines: list[str] = [
        f"# {DIGEST_TITLE}",
        "",
        f"_Generated {generated_at}. Read-only report. can_execute=false._",
        "",
        "## Merged in the last 24h",
        *_pr_lines(merged_last_24h, empty_label="none"),
        "",
        "## On HOLD",
        *_pr_lines(hold_prs, empty_label="none", reason_key="reason"),
        "",
        "## Independent handoff or CODEOWNER decision (release scoped; other lanes continue)",
        *_pr_lines(waiting_on_owner, empty_label="none", reason_key="reason"),
        "",
        "## Stale or conflicted",
        *_pr_lines(stale_or_conflicted, empty_label="none", reason_key="reason"),
        "",
        "## State",
        f"- last main SHA: `{last_main_sha}`",
        f"- last Render deployed SHA: `{last_render_deployed_sha}`",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-json", required=True, help="Path to a JSON file with the digest evidence")
    args = parser.parse_args(argv)
    with open(args.evidence_json, "r", encoding="utf-8") as fh:
        evidence = json.load(fh)
    print(render_digest(**evidence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
