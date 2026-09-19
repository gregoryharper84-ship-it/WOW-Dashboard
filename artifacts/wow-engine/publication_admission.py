"""V17 canonical publication admission gate.

A recommendation row's ``probability_publishable``/``calibrated_probability``
fields are caller-attested text at the moment they arrive over HTTP. This
module proves or rejects that attestation against the frozen, immutable
governed prediction row it claims to come from, instead of trusting it.

It never computes, recalculates, or repairs a sporting probability. It never
upgrades a row's terminal label. A row that fails admission is simply not
admitted for calibration or downstream card consumption; it is not deleted,
and its own recorded terminal_label is never upgraded or overwritten by this
gate. The caller persisting the row may still record admission blockers
alongside it -- that is additive evidence, not a change to the verdict this
module reaches.

Patch: V17-PUBLICATION-ADMISSION-GATE (recovery rebuild, 2026-09-19)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Any, Callable, Iterable, Mapping

CAN_EXECUTE = False

# Every table this gate is permitted to read a governed prediction receipt
# from. A caller names the table on the recommendation row; it must not be
# allowed to point the admission gate at an arbitrary/ungoverned table.
GOVERNED_PREDICTION_TABLES = frozenset(
    {
        "wow_predictions",
        "wow_event_predictions",
        "wow_nfl_event_predictions",
        "wow_ncaaf_predictions",
        "wow_live_probability_predictions",
        "wow_kalshi_weather_predictions",
        "wow_mlb_1ip_bf_shadow_predictions",
    }
)

# Each governed prediction table's own primary key column. These differ across
# tables (schema history, not this gate's choice), so a lookup must use the
# right column per table rather than assuming "prediction_id" everywhere.
GOVERNED_PREDICTION_PK_COLUMNS: dict[str, str] = {
    "wow_predictions": "prediction_id",
    "wow_event_predictions": "event_prediction_id",
    "wow_nfl_event_predictions": "event_prediction_id",
    "wow_ncaaf_predictions": "ncaaf_prediction_id",
    "wow_live_probability_predictions": "prediction_id",
    "wow_kalshi_weather_predictions": "prediction_id",
    "wow_mlb_1ip_bf_shadow_predictions": "prediction_id",
}

# A terminal label carrying any of these markers is research/interest/holding
# evidence, never a publication-eligible verdict, regardless of what a caller
# otherwise claims about the row.
_NON_PUBLISHABLE_LABEL_MARKERS = (
    "RESEARCH",
    "HOLD",
    "SCOUT",
    "WATCH",
    "INTEREST",
    "NOT_RANK_ELIGIBLE",
    "REJECT",
    "NO_PLAY",
)

_IDENTITY_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "event_id": ("event_id",),
    "participant": ("participant", "player", "team"),
    "stat_type": ("stat_type", "market_type", "market_family"),
}
_OPTIONAL_EXACT_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "line": ("line",),
    "direction": ("direction",),
}


@dataclass(frozen=True)
class PublicationAdmission:
    admitted: bool
    blockers: tuple[str, ...]
    governed_prediction_id: str | None
    governed_prediction_table: str | None
    can_execute: bool = False


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text.casefold() if text else None


def _first(mapping: Mapping[str, Any], aliases: tuple[str, ...]) -> Any:
    for key in aliases:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _probability(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _aware(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _compare_identity(row: Mapping[str, Any], prediction: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    for dimension, aliases in _IDENTITY_FIELD_ALIASES.items():
        left = _text(_first(row, aliases))
        right = _text(_first(prediction, aliases))
        if left is None or right is None:
            blockers.append(f"PUBLICATION_IDENTITY_INCOMPLETE:{dimension}")
        elif left != right:
            blockers.append(f"PUBLICATION_EXACT_IDENTITY_MISMATCH:{dimension}")

    for dimension, aliases in _OPTIONAL_EXACT_FIELD_ALIASES.items():
        left_raw = _first(row, aliases)
        right_raw = _first(prediction, aliases)
        if left_raw is None and right_raw is None:
            continue
        if left_raw is None or right_raw is None:
            blockers.append(f"PUBLICATION_EXACT_IDENTITY_MISMATCH:{dimension}")
            continue
        if dimension == "line":
            left = _decimal(left_raw)
            right = _decimal(right_raw)
            if left is None or right is None or left != right:
                blockers.append("PUBLICATION_EXACT_IDENTITY_MISMATCH:line")
        elif _text(left_raw) != _text(right_raw):
            blockers.append(f"PUBLICATION_EXACT_IDENTITY_MISMATCH:{dimension}")

    return blockers


def _final_refresh_blockers(row: Mapping[str, Any], *, now: datetime | None) -> list[str]:
    """The host, not this backend, observes final pregame state (lineup,
    status, price freshness) immediately before publication. wow_predictions
    carries no per-row final-refresh column, so that evidence must travel on
    the recommendation row itself; its absence fails closed rather than being
    treated as an implicit pass."""
    blockers: list[str] = []
    final_refresh = row.get("final_refresh")
    if not isinstance(final_refresh, Mapping):
        return ["PUBLICATION_FINAL_REFRESH_EVIDENCE_MISSING"]

    status = _text(final_refresh.get("status"))
    if status != "pass":
        blockers.append(f"PUBLICATION_FINAL_REFRESH_NOT_PASS:{final_refresh.get('status') or 'MISSING'}")

    checked_at = _aware(final_refresh.get("checked_at"))
    if checked_at is None:
        blockers.append("PUBLICATION_FINAL_REFRESH_CHECKED_AT_MISSING")
    elif now is not None and checked_at > now:
        blockers.append("PUBLICATION_FINAL_REFRESH_CHECKED_AT_IN_FUTURE")

    return blockers


def fetch_governed_predictions(
    get_client_fn: Callable[[], Any],
    claims: Iterable[tuple[str | None, str | None]],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Fetch every distinct (table, id) claim in one query per recognized
    table. Unrecognized tables and missing ids are skipped here -- they still
    surface as blockers from ``evaluate_publication_eligibility`` because it
    will find nothing for them in the returned mapping.
    """
    by_table: dict[str, set[str]] = {}
    for table, prediction_id in claims:
        if not table or not prediction_id or table not in GOVERNED_PREDICTION_TABLES:
            continue
        by_table.setdefault(table, set()).add(str(prediction_id))

    if not by_table:
        return {}

    client = get_client_fn()
    fetched: dict[tuple[str, str], dict[str, Any]] = {}
    for table, ids in by_table.items():
        pk_column = GOVERNED_PREDICTION_PK_COLUMNS[table]
        rows = client.table(table).select("*").in_(pk_column, sorted(ids)).execute().data or []
        for row in rows:
            row_id = row.get(pk_column)
            if row_id is not None:
                fetched[(table, str(row_id))] = row
    return fetched


def evaluate_publication_eligibility(
    row: Mapping[str, Any],
    prediction: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
) -> PublicationAdmission:
    """Prove one recommendation row's publication claim against its named
    governed prediction row.

    ``prediction`` must already have been fetched, by primary key, from
    ``row['governed_prediction_table']`` -- this function performs no I/O and
    trusts nothing about how the caller obtained it beyond that it is the
    literal frozen database row (or ``None`` if no matching row exists).
    """
    governed_prediction_id = _first(row, ("governed_prediction_id",))
    governed_prediction_table = _first(row, ("governed_prediction_table",))
    gid = str(governed_prediction_id) if governed_prediction_id is not None else None
    gtable = str(governed_prediction_table) if governed_prediction_table is not None else None

    if not row.get("probability_publishable"):
        # A row that never claimed publication cannot be admitted for
        # calibration or card consumption, but it is not an error -- it is
        # simply unpublishable research/interest/holding evidence.
        return PublicationAdmission(False, ("PUBLICATION_NOT_CLAIMED",), gid, gtable, False)

    blockers: list[str] = []

    terminal_label = str(row.get("terminal_label") or "").upper()
    if any(marker in terminal_label for marker in _NON_PUBLISHABLE_LABEL_MARKERS):
        blockers.append(f"PUBLICATION_TERMINAL_LABEL_NOT_ELIGIBLE:{terminal_label or 'MISSING'}")

    if not gid:
        blockers.append("PUBLICATION_GOVERNED_PREDICTION_ID_MISSING")
    if not gtable:
        blockers.append("PUBLICATION_GOVERNED_PREDICTION_TABLE_MISSING")
    elif gtable not in GOVERNED_PREDICTION_TABLES:
        blockers.append(f"PUBLICATION_GOVERNED_PREDICTION_TABLE_NOT_RECOGNIZED:{gtable}")

    if prediction is None:
        blockers.append("PUBLICATION_NO_MATCHING_GOVERNED_PREDICTION_ROW")
        return PublicationAdmission(False, tuple(dict.fromkeys(blockers)), gid, gtable, False)

    blockers.extend(_compare_identity(row, prediction))

    if prediction.get("probability_publishable") is not True:
        blockers.append("PUBLICATION_UPSTREAM_PREDICTION_NOT_PUBLISHABLE")
    if prediction.get("blockers"):
        blockers.append("PUBLICATION_UPSTREAM_PREDICTION_HAS_BLOCKERS")

    calibrated = _probability(prediction.get("calibrated_probability"))
    lower_bound = _probability(
        prediction.get("calibrated_probability_lower_bound", prediction.get("calibrated_lower_bound"))
    )
    if (
        calibrated is None
        or lower_bound is None
        or not (0.0 < calibrated < 1.0)
        or not (0.0 <= lower_bound <= calibrated)
    ):
        blockers.append("PUBLICATION_CALIBRATED_PROBABILITY_PACKAGE_INVALID")
    else:
        claimed_calibrated = _probability(row.get("calibrated_probability"))
        if claimed_calibrated is not None and abs(claimed_calibrated - calibrated) > 1e-9:
            blockers.append("PUBLICATION_CLAIMED_PROBABILITY_DOES_NOT_MATCH_GOVERNED_PREDICTION")
        claimed_lower = _probability(row.get("calibrated_probability_lower_bound"))
        if claimed_lower is not None and abs(claimed_lower - lower_bound) > 1e-9:
            blockers.append("PUBLICATION_CLAIMED_LOWER_BOUND_DOES_NOT_MATCH_GOVERNED_PREDICTION")

    event_start = _aware(row.get("event_start_time"))
    locked_at = _aware(prediction.get("locked_at") or prediction.get("created_at"))
    if event_start is None or locked_at is None or locked_at >= event_start:
        blockers.append("PUBLICATION_PREDICTION_NOT_PROVEN_PREGAME")

    blockers.extend(_final_refresh_blockers(row, now=now))

    return PublicationAdmission(len(blockers) == 0, tuple(dict.fromkeys(blockers)), gid, gtable, False)
