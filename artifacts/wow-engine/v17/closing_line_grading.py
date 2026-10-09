"""Grade settled WOW predictions against the captured closing line.

Measurement only. This module never produces, adjusts, or publishes a sporting
probability, and it never feeds market consensus, market role, rank
eligibility, or terminal labels. It answers one question for each settled
prediction: on the side the model selected, was the model's probability more
or less accurate than the no-vig closing market probability?

Close source: CLOSE reference rows (last captured pre-start quote per book)
from one configured provider, by default the free ESPN capture
(``espn_market_history``). These are captured references, not
provider-official closes; the semantics label is carried into every grade row.

Matching is exact and fail-closed: same sport, start time within a tight
window, and normalized full team names. Unmatched or ambiguous predictions are
counted with a typed status and are never guessed.
"""
from __future__ import annotations

import logging
import math
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from v17 import rundown_market_ledger as ledger

CAN_EXECUTE = False
PREDICTION_AUTHORITY = False
TABLE = "wow_closing_line_grades"
GRADING_VERSION = "CLOSING_LINE_GRADE_V1"
CLOSE_REFERENCE_SEMANTICS = "LAST_CAPTURED_PREMATCH_REFERENCE_NOT_PROVIDER_OFFICIAL_CLOSE"
MONEYLINE_MARKET_ID = "1"
START_MATCH_TOLERANCE = timedelta(minutes=20)

LINKED = "LINKED"
NO_CLOSE_EVENT = "NO_CLOSE_EVENT_MATCH"
AMBIGUOUS_EVENT = "AMBIGUOUS_CLOSE_EVENT_MATCH"
NO_TWO_SIDED_CLOSE = "NO_TWO_SIDED_CLOSE_PRICE"
INVALID_PREDICTION = "PREDICTION_FIELDS_INVALID"

SOURCE_MLB = "MLB_EVENT_PREDICTION"
SOURCE_NFL = "NFL_FORWARD_SHADOW_GRADE"
SPORT_KEYS = {"MLB": "baseball_mlb", "NFL": "americanfootball_nfl"}

LOGGER = logging.getLogger(__name__)


def _norm(name: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").casefold())


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _probability(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and 0.0 < parsed < 1.0 else None


def american_to_implied(odds: Any) -> float | None:
    """Raw implied probability (with vig) from American odds."""
    try:
        value = float(odds)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or -100.0 < value < 100.0:
        return None
    return 100.0 / (value + 100.0) if value > 0 else -value / (-value + 100.0)


def no_vig_pair(selected_odds: Any, opponent_odds: Any) -> float | None:
    """Selected side's no-vig probability from a two-way price pair."""
    selected = american_to_implied(selected_odds)
    opponent = american_to_implied(opponent_odds)
    if selected is None or opponent is None:
        return None
    total = selected + opponent
    return selected / total if total > 0 else None


def _close_rows_by_event(rows: Iterable[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    events: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        if str(row.get("snapshot_kind") or "").upper() != "CLOSE":
            continue
        if str(row.get("market_id") or "") != MONEYLINE_MARKET_ID:
            continue
        if row.get("is_live") is True or row.get("is_main_line") is False:
            continue
        event_id = str(row.get("provider_event_id") or "")
        if event_id:
            events.setdefault(event_id, []).append(row)
    return events


def match_close(
    *,
    selected: str,
    opponent: str | None,
    event_start: datetime,
    close_rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Find the selected side's consensus no-vig close. Fail closed on doubt."""
    want_selected = _norm(selected)
    want_opponent = _norm(opponent) if opponent else None
    candidates: list[tuple[str, list[Mapping[str, Any]]]] = []
    for event_id, rows in _close_rows_by_event(close_rows).items():
        start = _parse_dt(rows[0].get("event_start_utc"))
        if start is None or abs(start - event_start) > START_MATCH_TOLERANCE:
            continue
        names = {_norm(row.get("participant_name")) for row in rows}
        if want_selected not in names:
            continue
        if want_opponent is not None and want_opponent not in names:
            continue
        candidates.append((event_id, rows))

    if not candidates:
        return {"status": NO_CLOSE_EVENT}
    if len(candidates) > 1:
        return {"status": AMBIGUOUS_EVENT, "candidate_events": len(candidates)}

    event_id, rows = candidates[0]
    by_book: dict[str, dict[str, Mapping[str, Any]]] = {}
    for row in rows:
        book = str(row.get("affiliate_id") or row.get("sportsbook") or "")
        by_book.setdefault(book, {})[_norm(row.get("participant_name"))] = row

    book_probabilities: dict[str, float] = {}
    latest_quote: datetime | None = None
    for book, sides in by_book.items():
        if want_selected not in sides or len(sides) != 2:
            continue  # need exactly the two sides of this two-way market
        selected_row = sides[want_selected]
        opponent_row = next(row for key, row in sides.items() if key != want_selected)
        probability = no_vig_pair(selected_row.get("american_odds"), opponent_row.get("american_odds"))
        if probability is None:
            continue
        book_probabilities[book] = probability
        for row in (selected_row, opponent_row):
            quoted = _parse_dt(row.get("price_updated_at")) or _parse_dt(row.get("fetched_at"))
            if quoted is not None and (latest_quote is None or quoted > latest_quote):
                latest_quote = quoted

    if not book_probabilities:
        return {"status": NO_TWO_SIDED_CLOSE, "provider_event_id": event_id}
    consensus = sum(book_probabilities.values()) / len(book_probabilities)
    return {
        "status": LINKED,
        "provider_event_id": event_id,
        "close_probability": consensus,
        "books": sorted(book_probabilities),
        "book_probabilities": book_probabilities,
        "close_quote_at": _iso(latest_quote) if latest_quote else None,
    }


def pick_time_price(
    *,
    selected: str,
    provider_event_id: str,
    rows: Iterable[Mapping[str, Any]],
    at: datetime,
) -> dict[str, Any] | None:
    """Selected side's no-vig consensus from the latest captures at/before ``at``.

    Uses CURRENT captures of the same provider event (moneyline, main line,
    pregame). Each book needs both sides quoted at/before the pick time.
    """
    want = _norm(selected)
    latest: dict[str, dict[str, tuple[datetime, Mapping[str, Any]]]] = {}
    for row in rows:
        if str(row.get("provider_event_id") or "") != provider_event_id:
            continue
        if str(row.get("snapshot_kind") or "").upper() != "CURRENT":
            continue
        if str(row.get("market_id") or "") != MONEYLINE_MARKET_ID or row.get("is_live") is True or row.get("is_main_line") is False:
            continue
        quoted = _parse_dt(row.get("price_updated_at")) or _parse_dt(row.get("fetched_at"))
        if quoted is None or quoted > at:
            continue
        book = str(row.get("affiliate_id") or row.get("sportsbook") or "")
        name = _norm(row.get("participant_name"))
        current = latest.setdefault(book, {}).get(name)
        if current is None or quoted > current[0]:
            latest[book][name] = (quoted, row)
    probabilities: list[float] = []
    newest: datetime | None = None
    for sides in latest.values():
        if want not in sides or len(sides) != 2:
            continue
        opponent = next(value for key, value in sides.items() if key != want)
        probability = no_vig_pair(sides[want][1].get("american_odds"), opponent[1].get("american_odds"))
        if probability is None:
            continue
        probabilities.append(probability)
        for quoted, _row in (sides[want], opponent):
            newest = quoted if newest is None or quoted > newest else newest
    if not probabilities:
        return None
    return {
        "probability": sum(probabilities) / len(probabilities),
        "book_count": len(probabilities),
        "quote_at": _iso(newest) if newest else None,
    }


def _brier(probability: float, outcome: int) -> float:
    return (probability - outcome) ** 2


def _log_loss(probability: float, outcome: int) -> float:
    p = min(max(probability, 1e-12), 1 - 1e-12)
    return -math.log(p if outcome == 1 else 1 - p)


def grade_row(
    *,
    source: str,
    prediction_id: str,
    sport: str,
    official_event_id: str | None,
    selected: str,
    opponent: str | None,
    event_start: Any,
    model_probability: Any,
    outcome: Any,
    close_rows: Iterable[Mapping[str, Any]],
    predicted_at: Any = None,
) -> dict[str, Any]:
    """Build one grade (or typed non-link) for a settled prediction.

    ``close_rows`` may also carry CURRENT captures; they supply the price at
    pick time (``predicted_at``) for closing-line value. Only CLOSE rows are
    used for the close itself.
    """
    close_rows = list(close_rows)
    start = _parse_dt(event_start)
    p_model = _probability(model_probability)
    hit = outcome if outcome in (0, 1) and not isinstance(outcome, bool) else None
    base = {
        "prediction_source": source,
        "prediction_id": str(prediction_id),
        "sport": sport,
        "official_event_id": official_event_id,
        "selected_participant": selected,
        "event_start_utc": _iso(start) if start else None,
        "grading_version": GRADING_VERSION,
        "close_reference_semantics": CLOSE_REFERENCE_SEMANTICS,
        "prediction_authority": False,
        "can_execute": False,
    }
    if start is None or p_model is None or hit is None or not _norm(selected):
        return {**base, "link_status": INVALID_PREDICTION}

    match = match_close(selected=selected, opponent=opponent, event_start=start, close_rows=close_rows)
    if match["status"] != LINKED:
        return {**base, "link_status": match["status"]}

    p_close = match["close_probability"]
    picked_at = _parse_dt(predicted_at)
    pick = (
        pick_time_price(selected=selected, provider_event_id=match["provider_event_id"], rows=close_rows, at=picked_at)
        if picked_at is not None and picked_at <= start
        else None
    )
    # CLV on the side the model favoured: positive = the market moved toward
    # the model between the pick and the close.
    side_sign = 1.0 if p_model >= 0.5 else -1.0
    clv = side_sign * (p_close - pick["probability"]) if pick else None
    model_brier = _brier(p_model, hit)
    close_brier = _brier(p_close, hit)
    model_ll = _log_loss(p_model, hit)
    close_ll = _log_loss(p_close, hit)
    return {
        **base,
        "link_status": LINKED,
        "provider_event_id": match["provider_event_id"],
        "model_probability": p_model,
        "close_probability": p_close,
        "close_books": match["books"],
        "close_book_count": len(match["books"]),
        "close_quote_at": match["close_quote_at"],
        "outcome": hit,
        "model_brier": model_brier,
        "close_brier": close_brier,
        "brier_advantage_vs_close": close_brier - model_brier,
        "model_log_loss": model_ll,
        "close_log_loss": close_ll,
        "log_loss_advantage_vs_close": close_ll - model_ll,
        "model_minus_close": p_model - p_close,
        "predicted_at": _iso(picked_at) if picked_at else None,
        "pick_market_probability": pick["probability"] if pick else None,
        "pick_quote_at": pick["quote_at"] if pick else None,
        "pick_book_count": pick["book_count"] if pick else None,
        "clv_model_side": clv,
    }


# ---------------------------------------------------------------------------
# Source adapters (read-only)
# ---------------------------------------------------------------------------

def _rows(response: Any) -> list[dict[str, Any]]:
    data = getattr(response, "data", None)
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def mlb_settled_selections(client: Any, *, since: datetime) -> list[dict[str, Any]]:
    """Final pre-start MLB prediction per event, graded on the home side."""
    outcomes = _rows(
        client.table("wow_event_outcomes")
        .select("event_prediction_id,official_winner,void")
        .gte("created_at", _iso(since))
        .limit(5000)
        .execute()
    )
    settled = {
        str(row["event_prediction_id"]): row
        for row in outcomes
        if row.get("event_prediction_id") and not row.get("void") and row.get("official_winner")
    }
    if not settled:
        return []
    predictions: list[dict[str, Any]] = []
    ids = sorted(settled)
    for start in range(0, len(ids), 200):
        predictions.extend(
            _rows(
                client.table("wow_event_predictions")
                .select(
                    "event_prediction_id,created_at,sport,official_event_id,event_start_time,"
                    "home_team,away_team,calibrated_home_probability"
                )
                .in_("event_prediction_id", ids[start:start + 200])
                .execute()
            )
        )
    final: dict[str, dict[str, Any]] = {}
    for row in predictions:
        if str(row.get("sport") or "").upper() != "MLB":
            continue
        start_at = _parse_dt(row.get("event_start_time"))
        created = _parse_dt(row.get("created_at"))
        if start_at is None or created is None or created > start_at:
            continue  # only pregame predictions are graded against the close
        key = str(row.get("official_event_id") or row["event_prediction_id"])
        current = final.get(key)
        if current is None or created > _parse_dt(current["created_at"]):
            final[key] = row
    selections = []
    for row in final.values():
        outcome = settled[str(row["event_prediction_id"])]
        selections.append({
            "source": SOURCE_MLB,
            "prediction_id": str(row["event_prediction_id"]),
            "sport": "MLB",
            "official_event_id": row.get("official_event_id"),
            "selected": row.get("home_team") or "",
            "opponent": row.get("away_team"),
            "event_start": row.get("event_start_time"),
            "predicted_at": row.get("created_at"),
            "model_probability": row.get("calibrated_home_probability"),
            "outcome": 1 if _norm(outcome.get("official_winner")) == _norm(row.get("home_team")) else 0,
        })
    return selections


def nfl_settled_selections(client: Any, *, since: datetime) -> list[dict[str, Any]]:
    rows = _rows(
        client.table("wow_nfl_forward_shadow_grades")
        .select(
            "grade_id,official_event_id,event_start_time_utc,prediction_created_at,"
            "selected_participant,calibrated_probability,outcome"
        )
        .gte("created_at", _iso(since))
        .limit(5000)
        .execute()
    )
    selections = []
    for row in rows:
        try:
            outcome = int(row.get("outcome"))
        except (TypeError, ValueError):
            outcome = None
        selections.append({
            "source": SOURCE_NFL,
            "prediction_id": str(row.get("grade_id")),
            "sport": "NFL",
            "official_event_id": row.get("official_event_id"),
            "selected": row.get("selected_participant") or "",
            "opponent": None,
            "event_start": row.get("event_start_time_utc"),
            "predicted_at": row.get("prediction_created_at"),
            "model_probability": row.get("calibrated_probability"),
            "outcome": outcome,
        })
    return selections


def close_provider() -> str:
    """Single close source per run, so one game never matches two providers.

    Default is the free ESPN capture (owner direction 2026-10-09).
    """
    return os.getenv("WOW_CLOSING_LINE_PROVIDER", "ESPN").strip().upper() or "ESPN"


def _close_rows(client: Any, sport_key: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    return _rows(
        client.table(ledger.TABLE)
        .select(
            "provider_event_id,event_start_utc,market_id,participant_name,affiliate_id,sportsbook,"
            "american_odds,snapshot_kind,is_live,is_main_line,price_updated_at,fetched_at"
        )
        .eq("provider", close_provider())
        .eq("sport_key", sport_key)
        .in_("snapshot_kind", ["CURRENT", "CLOSE"])
        .gte("event_start_utc", _iso(start))
        .lt("event_start_utc", _iso(end))
        .limit(20000)
        .execute()
    )


def _already_linked(client: Any) -> set[tuple[str, str]]:
    rows = _rows(client.table(TABLE).select("prediction_source,prediction_id").limit(100000).execute())
    return {(str(row["prediction_source"]), str(row["prediction_id"])) for row in rows}


def run_closing_line_grading(
    client: Any,
    *,
    now: datetime | None = None,
    lookback_days: int = 14,
) -> dict[str, Any]:
    """Link newly settled predictions to captured closes. Append-only, idempotent.

    Only LINKED grades are persisted. Non-links are counted by typed status and
    retried next cycle (a CLOSE reference can be materialized after settlement).
    """
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    since = current - timedelta(days=lookback_days)
    done = _already_linked(client)
    summary: dict[str, Any] = {"status": "COMPLETE", "by_sport": {}, "prediction_authority": False, "can_execute": False}
    to_write: list[dict[str, Any]] = []
    for sport, loader in (("MLB", mlb_settled_selections), ("NFL", nfl_settled_selections)):
        selections = [s for s in loader(client, since=since) if (s["source"], s["prediction_id"]) not in done]
        counts: dict[str, int] = {}
        starts = [_parse_dt(s["event_start"]) for s in selections]
        starts = [s for s in starts if s is not None]
        close_rows = (
            _close_rows(client, SPORT_KEYS[sport], min(starts) - timedelta(days=1), max(starts) + timedelta(days=1))
            if starts else []
        )
        for selection in selections:
            grade = grade_row(
                source=selection["source"],
                prediction_id=selection["prediction_id"],
                sport=sport,
                official_event_id=selection["official_event_id"],
                selected=selection["selected"],
                opponent=selection["opponent"],
                event_start=selection["event_start"],
                model_probability=selection["model_probability"],
                outcome=selection["outcome"],
                close_rows=close_rows,
                predicted_at=selection.get("predicted_at"),
            )
            counts[grade["link_status"]] = counts.get(grade["link_status"], 0) + 1
            if grade["link_status"] == LINKED:
                to_write.append(grade)
        summary["by_sport"][sport] = {"pending_selections": len(selections), "statuses": counts}
    if to_write:
        client.table(TABLE).upsert(
            to_write, on_conflict="prediction_source,prediction_id", ignore_duplicates=True
        ).execute()
    summary["grades_written"] = len(to_write)
    return summary


__all__ = [
    "CAN_EXECUTE",
    "PREDICTION_AUTHORITY",
    "TABLE",
    "american_to_implied",
    "grade_row",
    "match_close",
    "no_vig_pair",
    "run_closing_line_grading",
]
