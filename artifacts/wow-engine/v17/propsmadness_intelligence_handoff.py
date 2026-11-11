"""Offline PropsMadness discovery -> shared LLP / prop *research* handoff.

No network, scheduling, provider entitlement, history storage, model inference, or
production writes. Unverified public-page facts remain nonauthoritative hints.
This is intentionally NOT a feature hydrator or a probability adapter.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import math
import re
from typing import Any, Mapping, Sequence

from v17.propsmadness_multisport_public_explore import MARKETS

SCHEMA = "WOW_PM_SHARED_INTELLIGENCE_RESEARCH_V1"
SOURCE_SCHEMA = "WOW_PM_MULTISPORT_PUBLIC_DISCOVERY_V1"
SOURCE_STATUS = {"PUBLIC_ROWS_PARSED_DISCOVERY_ONLY", "PUBLIC_HTML_NO_VERIFIED_ROWS"}
SOURCE_RIGHTS = "PERSONAL_NONCOMMERCIAL_TERMS_REVIEW_REQUIRED"
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
ALIAS = re.compile(r"[0-9]{1,12}\Z")
PROVIDER = "PROPSMADNESS_PUBLIC_EXPLORE"
_FALSE_FLAGS = (
    "historical_dataset_materialized", "historical_training_authorized",
    "market_evidence_certified", "prediction_authority",
    "probability_publishable", "can_execute",
)
_ROW_FALSE_FLAGS = (
    "pregame_verified", "historical_line_evidence",
    "exact_line_market_authority", "can_execute",
)


class IntelligenceHandoffHold(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise IntelligenceHandoffHold(code)


def _utc(value: Any) -> str:
    _require(isinstance(value, str), "PM_HANDOFF_TIMESTAMP_INVALID")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise IntelligenceHandoffHold("PM_HANDOFF_TIMESTAMP_INVALID") from exc
    _require(dt.utcoffset() is not None, "PM_HANDOFF_TIMESTAMP_INVALID")
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _number(value: Any, code: str, *, allow_none: bool = False) -> float | None:
    if allow_none and value is None:
        return None
    _require(type(value) in (float, int) and math.isfinite(value) and value >= 0, code)
    return float(value)


def build_shared_intelligence_handoff(receipts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Compile existing parser receipts; never request a page or claim rights.

    Only public Explore discovery receipts are accepted. Empty source receipts are
    reconciled, not replaced. Provider match IDs remain *aliases*, so LLP gets
    event research leads, never team strength, game scripts or probability.
    Props receive display-only research, never an exact-line betting quote.
    """
    _require(isinstance(receipts, (list, tuple)) and 0 < len(receipts) <= 16,
             "PM_HANDOFF_RECEIPTS_INVALID")
    provenance: list[dict[str, Any]] = []
    prop_hints: list[dict[str, Any]] = []
    event_hints: dict[tuple[str, str], dict[str, Any]] = {}
    seen_pages: set[tuple[str, str]] = set()
    seen_theses: set[tuple[str, str, str, str]] = set()
    source_rows = 0

    for receipt in receipts:
        _require(isinstance(receipt, Mapping), "PM_HANDOFF_RECEIPT_SCHEMA_INVALID")
        _require(receipt.get("schema") == SOURCE_SCHEMA
                 and receipt.get("status") in SOURCE_STATUS,
                 "PM_HANDOFF_SOURCE_CONTRACT_INVALID")
        _require(all(receipt.get(field) is False for field in _FALSE_FLAGS)
                 and receipt.get("source_rights_state") == SOURCE_RIGHTS,
                 "PM_HANDOFF_SOURCE_AUTHORITY_CONFLICT")
        sport, market = receipt.get("sport"), receipt.get("market")
        _require(isinstance(sport, str) and sport in MARKETS
                 and isinstance(market, str) and market in MARKETS[sport],
                 "PM_HANDOFF_ROUTE_UNSUPPORTED")
        page = (sport, market)
        _require(page not in seen_pages, "PM_HANDOFF_DUPLICATE_SOURCE_PAGE")
        seen_pages.add(page)
        stat = MARKETS[sport][market][0]
        url = f"https://propsmadness.com/{sport.lower()}/props/{market}"
        digest = receipt.get("html_sha256")
        _require(receipt.get("source_url") == url and isinstance(digest, str)
                 and HEX64.fullmatch(digest) is not None,
                 "PM_HANDOFF_PROVENANCE_INVALID")
        rows = receipt.get("rows")
        _require(isinstance(rows, list) and type(receipt.get("row_count")) is int
                 and len(rows) == receipt["row_count"],
                 "PM_HANDOFF_RECONCILIATION_INVALID")
        _require((not rows) == (receipt["status"] == "PUBLIC_HTML_NO_VERIFIED_ROWS"),
                 "PM_HANDOFF_SOURCE_CONTRACT_INVALID")
        source_rows += len(rows)
        provenance.append({
            "sport": sport, "market": market, "source_url": url,
            "html_sha256": digest, "source_rows": len(rows),
            "rights_state": SOURCE_RIGHTS, "source_status": receipt["status"],
        })
        for row in rows:
            _require(isinstance(row, Mapping) and row.get("sport") == sport
                     and row.get("stat_type") == stat and row.get("period") == "FULL_GAME"
                     and row.get("source_provider") == PROVIDER
                     and row.get("source_page") == url,
                     "PM_HANDOFF_ROW_IDENTITY_INVALID")
            _require(all(row.get(field) is False for field in _ROW_FALSE_FLAGS)
                     and all(row.get(field) is None for field in (
                         "canonical_player_id", "canonical_event_id", "event_start_utc",
                         "sportsbook", "model_probability", "calibrated_lower_bound",
                     )), "PM_HANDOFF_ROW_AUTHORITY_CONFLICT")
            pid, mid = row.get("provider_player_id"), row.get("provider_match_id")
            _require(isinstance(pid, str) and ALIAS.fullmatch(pid) is not None
                     and isinstance(mid, str) and ALIAS.fullmatch(mid) is not None,
                     "PM_HANDOFF_PROVIDER_ALIAS_INVALID")
            _require(isinstance(row.get("display_name_unverified"), str)
                     and bool(row["display_name_unverified"].strip())
                     and isinstance(row.get("fixture_text"), str),
                     "PM_HANDOFF_ROW_IDENTITY_INVALID")
            line = _number(row.get("display_line"), "PM_HANDOFF_LINE_INVALID")
            _require(0 < line <= 1000, "PM_HANDOFF_LINE_INVALID")
            avg = _number(row.get("l10_avg_display"),
                          "PM_HANDOFF_AVERAGE_INVALID", allow_none=True)
            odds = []
            for key in ("over_odds_display", "under_odds_display"):
                value = row.get(key)
                _require(value is None or (type(value) is int and -10000 <= value <= 10000),
                         "PM_HANDOFF_DISPLAY_ODDS_INVALID")
                odds.append(value)
            captured = _utc(row.get("capture_utc"))
            thesis = sport, mid, pid, stat
            _require(thesis not in seen_theses, "PM_HANDOFF_DUPLICATE_THESIS")
            seen_theses.add(thesis)
            reference = hashlib.sha256(
                f"{sport}|{mid}|{pid}|{stat}|{captured}|{digest}".encode()
            ).hexdigest()
            prop_hints.append({
                "research_reference": reference, "sport": sport, "stat_type": stat,
                "period": "FULL_GAME", "provider_player_alias": pid,
                "provider_event_alias": mid,
                "player_display_unverified": row["display_name_unverified"].strip(),
                "display_line_unverified": line, "display_l10_average_unverified": avg,
                "over_odds_unverified": odds[0], "under_odds_unverified": odds[1],
                "fixture_text_unverified": row["fixture_text"],
                "captured_at": captured, "source_url": url, "html_sha256": digest,
                "evidence_domain": "DISCOVERY_RESEARCH_ONLY",
                "exact_line_market_authority": False,
                "official_identity_verified": False, "pregame_feature_eligible": False,
                "model_probability": None, "calibrated_lower_bound": None,
                "can_execute": False,
            })
            evkey = sport, mid
            event = event_hints.setdefault(evkey, {
                "sport": sport, "provider_event_alias": mid,
                "official_event_id": None, "status": "EVENT_IDENTITY_UNVERIFIED",
                "fixture_labels_unverified": set(), "prop_research_references": [],
                "sporting_features": [], "model_probability": None,
                "calibrated_lower_bound": None, "can_execute": False,
            })
            event["fixture_labels_unverified"].add(row["fixture_text"])
            event["prop_research_references"].append(reference)

    llp = []
    for (sport, mid), event in sorted(event_hints.items()):
        event["fixture_labels_unverified"] = sorted(event["fixture_labels_unverified"])
        event["prop_research_references"].sort()
        llp.append(event)
    return {
        "schema": SCHEMA, "status": "RESEARCH_ONLY_NO_MODEL_AUTHORITY",
        "source_receipts": len(provenance), "source_rows": source_rows,
        "unique_prop_hints": len(prop_hints), "unique_event_hints": len(llp),
        "source_provenance": provenance,
        "prop_discovery_hints": prop_hints,
        "llp_event_research_hints": llp,
        "verified_sporting_features": [], "model_consumption_receipts": [],
        "source_rights_verified": False, "historical_training_authorized": False,
        "prediction_authority": False, "probability_publishable": False,
        "production_write_authorized": False, "can_execute": False,
    }
