"""V17 zero-subscription source inventory for research/data engineering.

This module is a static routing and coverage audit, NOT a source fetcher or a
license, probability, historical-line, scoring, or production-readiness grant.
No network, storage, session access, or betting execution occurs here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from v17.model_source_entitlements import source_readiness
from v17.propsmadness_multisport_public_explore import MARKETS

CAN_EXECUTE = False
CATALOG_VERSION = "WOW_V17_FREE_SOURCE_CATALOG_V1"
PURPOSES = frozenset(("HISTORY", "STATUS", "DISCOVERY", "HISTORICAL_RESEARCH", "MARKET"))
SPORTS = frozenset((
    "NFL", "NBA", "NCAAF", "NCAAB", "MLB", "NHL", "WNBA", "SOCCER",
    "TENNIS", "PGA", "MMA", "BOXING", "CRICKET",
))


@dataclass(frozen=True)
class FreeSource:
    source_id: str
    sports: tuple[str, ...]
    purposes: tuple[str, ...]
    integration: str
    adapter_reference: str
    rights_state: str
    registry_source_id: str | None = None

    def __post_init__(self) -> None:
        if not self.source_id or not set(self.sports) <= SPORTS:
            raise ValueError("invalid source/sport inventory")
        if not set(self.purposes) <= PURPOSES:
            raise ValueError("invalid purpose inventory")


# Ordered by existing coverage first, then experimental/optional alternatives.
SOURCES = (
    FreeSource("NFLVERSE", ("NFL",), ("HISTORY", "STATUS"),
               "EXISTING", "nflverse_historical_adapter.py", "SOURCE_REVIEW_REQUIRED"),
    FreeSource("SPORTSDATAVERSE_ESPN", ("NBA", "WNBA", "NCAAB"),
               ("HISTORY", "STATUS"), "EXISTING",
               "basketball_event_hydration_runtime.py", "REGISTRY_CANDIDATE_REVIEW_REQUIRED",
               "SPORTSDATAVERSE_ESPN"),
    FreeSource("SPORTSDATAVERSE_WNBA_STATS", ("WNBA",), ("HISTORY",),
               "EXISTING", "wnba_training_source.py", "REGISTRY_CANDIDATE_REVIEW_REQUIRED",
               "SPORTSDATAVERSE_WNBA_STATS"),
    FreeSource("CFBD", ("NCAAF",), ("HISTORY", "STATUS"),
               "EXISTING_CREDENTIAL_REQUIRED", "ncaaf_cfbd_client.py",
               "CREDENTIAL_AND_SOURCE_REVIEW_REQUIRED", "CFBD"),
    FreeSource("MLB_STATS_API", ("MLB",), ("HISTORY", "STATUS"),
               "EXISTING", "artifacts/flask-scoring-api/app.py",
               "SOURCE_REVIEW_REQUIRED"),
    FreeSource("PYBASEBALL_STATCAST", ("MLB",), ("HISTORY",),
               "EXISTING_FALLBACK_WITH_ACCESS_FAILURES",
               ".agents/memory/mlb-pitcher-data-sources.md",
               "SOURCE_AND_ENDPOINT_REVIEW_REQUIRED"),
    FreeSource("NHL_PUBLIC_WEB_API", ("NHL",), ("HISTORY", "STATUS"),
               "CANDIDATE_REVIEW", "v17/model_source_entitlements.py",
               "REGISTRY_CANDIDATE_REVIEW_REQUIRED", "NHL_PUBLIC_WEB_API"),
    FreeSource("PROPSMADNESS_PUBLIC_EXPLORE", tuple(MARKETS),
               ("DISCOVERY",), "EXISTING_OFFLINE_RESEARCH",
               "v17/propsmadness_multisport_public_explore.py",
               "PERSONAL_NONCOMMERCIAL_TERMS_REVIEW_REQUIRED"),
    FreeSource("PROPSMADNESS_ACCOUNT_VIEW", tuple(MARKETS),
               ("HISTORICAL_RESEARCH", "DISCOVERY"), "EXISTING_OFFLINE_RESEARCH",
               "v17/propsmadness_nfl_account_view_adapter.py",
               "AUTHENTICATED_REUSE_AND_STORAGE_GRANT_UNVERIFIED"),
    FreeSource("THE_ODDS_API", ("NFL", "NBA", "NCAAF", "NCAAB", "MLB",
                              "NHL", "WNBA", "SOCCER"),
               ("MARKET", "DISCOVERY"), "EXISTING_QUOTA_LIMITED",
               "artifacts/flask-scoring-api/services/odds_api.py",
               "MARKET_TERMS_REVIEW_REQUIRED"),
    FreeSource("THERUNDOWN", ("NFL", "NBA", "NCAAF", "NCAAB", "MLB",
                             "NHL", "WNBA", "SOCCER", "TENNIS"),
               ("MARKET",), "EXISTING_FALLBACK_CREDENTIAL_REQUIRED",
               "artifacts/flask-scoring-api/services/odds_api.py",
               "MARKET_TERMS_REVIEW_REQUIRED", "THERUNDOWN"),
    FreeSource("ODDSPAPI", ("NFL", "NBA", "NCAAF", "NCAAB", "MLB",
                           "NHL", "WNBA", "SOCCER"),
               ("MARKET",), "UNVERIFIED_EXPERIMENT",
               "https://oddspapi.io/", "HISTORICAL_ARCHIVE_AND_RIGHTS_UNVERIFIED"),
)

_SOURCE_IDS = tuple(s.source_id for s in SOURCES)
if len(_SOURCE_IDS) != len(set(_SOURCE_IDS)):
    raise RuntimeError("duplicate V17 source inventory IDs")
if set(MARKETS) != {"NBA", "NFL", "MLB", "WNBA"}:
    raise RuntimeError("PropsMadness four-sport contract drift")


def compile_free_source_plan(sport: str, purpose: str) -> dict:
    """Inventory routes without fetching, certifying, or promoting data.

    Registry preflight is a training *candidate* status only; even a ready
    registry route cannot supply a live observation or calibrated probability.
    """
    if sport not in SPORTS or purpose not in PURPOSES:
        raise ValueError("unsupported sport or source purpose")
    routes = []
    for source in SOURCES:
        if sport not in source.sports or purpose not in source.purposes:
            continue
        row = asdict(source)
        reg = (source_readiness(source.registry_source_id)
               if source.registry_source_id is not None else None)
        row.update({
            "catalogued_only": True,
            "live_source_health": "NOT_PROBED",
            "registry_candidate_training_ready": (
                reg.ready_for_candidate_training if reg else False
            ),
            "registry_preflight_blockers": list(reg.blockers) if reg else [],
            "source_rights_independently_attested": False,
            "exact_historical_market_line_verified": False,
            "official_identity_verified": False,
            "training_authorized_by_catalog": False,
            "pregame_model_feature_eligible": False,
            "model_probability": None,
            "calibrated_lower_bound": None,
            "probability_publishable": False,
            "can_execute": False,
        })
        if source.source_id == "PROPSMADNESS_ACCOUNT_VIEW":
            row["historical_chart_threshold_is_closing_line"] = False
            row["account_view_validation"] = (
                "NFL_VIEW_OBSERVED_NBA_MLB_WNBA_LAYOUTS_SYNTHETIC"
            )
            row["recurring_authenticated_collection_authorized"] = False
            row["historical_storage_authorized"] = False
        routes.append(row)
    return {
        "schema": CATALOG_VERSION, "sport": sport, "purpose": purpose,
        "status": "SOURCE_INVENTORY_ONLY",
        "source_count": len(routes), "sources": routes,
        "source_data_acquired": False,
        "source_rights_independently_attested": False,
        "historical_training_authorized_by_catalog": False,
        "model_consumption_receipts": [],
        "model_probability": None, "calibrated_lower_bound": None,
        "probability_publishable": False, "can_execute": False,
    }
