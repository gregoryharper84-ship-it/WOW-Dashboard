"""Choose a news-discovery sport from the existing *future* Scout event slate.

Research routing only. No network calls, headline verification or model scoring.
One sport per run preserves the free-source request budget.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

ACTIVE_HORIZON_HOURS = 36


def select_active_slate(payload: object, at: datetime, supported: tuple[str, ...]) -> tuple[str | None, dict[str, int]]:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("NEWS_SELECTION_TIME_REQUIRES_TZ")
    if not isinstance(payload, dict) or not isinstance(payload.get("model_handoff"), dict):
        return None, {}
    seen: dict[str, set[str]] = {}
    start_window = at.astimezone(timezone.utc)
    end_window = start_window + timedelta(hours=ACTIVE_HORIZON_HOURS)
    handoff = payload["model_handoff"]
    for lane in ("team_event_candidates", "prop_candidates"):
        rows = handoff.get(lane)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            sport, event_id = row.get("sport_key"), row.get("official_event_id")
            if not isinstance(sport, str) or sport not in supported:
                continue
            if not isinstance(event_id, str) or not event_id.strip():
                continue
            raw = row.get("event_start_utc") or row.get("commence_time")
            if not raw and isinstance(row.get("event_request"), dict):
                raw = row["event_request"].get("event_start_time_utc")
            if not isinstance(raw, str):
                continue
            try:
                event_start = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            if event_start.tzinfo is None or event_start.utcoffset() is None:
                continue
            event_start = event_start.astimezone(timezone.utc)
            if start_window <= event_start <= end_window:
                seen.setdefault(sport, set()).add(event_id.strip())
    counts = {key: len(ids) for key, ids in seen.items() if ids}
    if not counts:
        return None, {}
    ordered = sorted(counts, key=lambda k: (-counts[k], k))
    # A daily job rotates among active sports, rather than always taking NBA.
    index = (at.date().toordinal() + at.hour // 6) % len(ordered)
    return ordered[index], counts
