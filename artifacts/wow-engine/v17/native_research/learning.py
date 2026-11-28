"""WOW Research Learning: deterministic research-performance evidence.

Scores which strategies and sources actually move blockers forward. The
scores only reorder discovery; they never change code, probabilities or
promotion decisions.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from statistics import median
from typing import Any, Mapping, Sequence

from .contract import PROGRESS_STAGE


def _t(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def strategy_scores(attempts: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per (source type, strategy): attempts, candidates yielded, verified yields, failures."""
    stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"runs": 0, "candidates": 0, "verified": 0,
                                                             "failures": 0})
    for attempt in attempts:
        for item in attempt.get("strategies") or []:
            key = f"{attempt.get('required_source_type')}:{item.get('strategy')}"
            s = stats[key]
            s["runs"] += 1
            s["candidates"] += int(item.get("candidates") or 0)
            s["verified"] += int(item.get("verified") or 0)
            s["failures"] += 1 if item.get("error") else 0
    for s in stats.values():
        # Laplace-smoothed yield of verified sources per run.
        s["score"] = round((s["verified"] + 1) / (s["runs"] + 2), 4)
    return dict(stats)


def order_strategies(source_type: str, permitted: Sequence[str], attempts: Sequence[Mapping[str, Any]]
                     ) -> list[str]:
    scores = strategy_scores(attempts)
    index = {name: i for i, name in enumerate(permitted)}
    return sorted(permitted, key=lambda s: (-scores.get(f"{source_type}:{s}", {}).get("score", 0.5), index[s]))


def source_reliability(findings: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """Share of a source's checks that verified fresh and conflict-free (smoothed)."""
    seen: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for f in findings:
        if f.get("finding_type") != "CANDIDATE_SOURCE" and f.get("finding_type") != "INCUMBENT_SOURCE_STATUS":
            continue
        ok = f.get("freshness") == "FRESH" and f.get("conflict_status") in ("CONSISTENT", "REFERENCE_SOURCE")
        seen[f["source_id"]][0] += int(ok)
        seen[f["source_id"]][1] += 1
    return {src: round((ok + 1) / (n + 2), 4) for src, (ok, n) in seen.items()}


def resolution_summary(attempts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Time from a task's first attempt to its best progress stage, and repeated failures."""
    by_task: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for a in attempts:
        by_task[a["task_id"]].append(a)
    durations, repeated_failures = [], 0
    for rows in by_task.values():
        rows = sorted(rows, key=lambda a: _t(a["finished_at"]))
        best = max(PROGRESS_STAGE.get(a.get("outcome"), 0) for a in rows)
        first_best = next(a for a in rows if PROGRESS_STAGE.get(a.get("outcome"), 0) == best)
        durations.append((_t(first_best["finished_at"]) - _t(rows[0]["started_at"])).total_seconds())
        repeated_failures += sum(1 for a in rows if PROGRESS_STAGE.get(a.get("outcome"), 0) == 0) > 1
    return {"tasks": len(by_task), "median_seconds_to_best_stage": median(durations) if durations else None,
            "tasks_with_repeated_failures": repeated_failures}
