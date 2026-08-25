"""
skills/orchestrator.py
WOW v16 Skills Pack — deterministic orchestration layer.

Enforces ORCHESTRATOR.md ordering, lowest-ceiling propagation,
routing rules, Reliability Freeze caps, and DB persistence.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from .contracts import SkillLabel, lower_ceiling, DRY_RUN_LABEL
from .registry import SkillRegistry

# ── Routing constants ──────────────────────────────────────────────────────────
KALSHI_SPORTS_STOP_SIGNAL   = "INVENTORY_EMPTY"
KALSHI_SPORTS_SCAN_SIGNAL   = "INVENTORY_READY"

# Reliability Freeze combo hard-reject
RELIABILITY_FREEZE_MAX_KALSHI_MARKETS = 3  # 4+ = hard reject

# Ordered execution sequences per market type (ORCHESTRATOR.md)
_ROUTE_SPORTS_TEAM = [
    "wow.sports-research-analyst",
    "wow.historical-trend-researcher",
    "wow.game-script-simulator",
    "wow.market-odds-intelligence",
    "wow.probability-ev-auditor",
    "wow.bankroll-risk-manager",
    "wow.governed-red-team-reviewer",  # advisory downgrade-only; no-op when no review_packet
    "wow.qa-hallucination-auditor",
]
_ROUTE_PLAYER_PROP = [
    "wow.sports-research-analyst",
    "wow.player-prop-intelligence",
    "wow.historical-trend-researcher",
    "wow.game-script-simulator",
    "wow.market-odds-intelligence",
    "wow.probability-ev-auditor",
    "wow.correlation-slip-auditor",
    "wow.bankroll-risk-manager",
    "wow.governed-red-team-reviewer",  # advisory downgrade-only; no-op when no review_packet
    "wow.qa-hallucination-auditor",
]
# Dedicated governance review route: used when market_type="governance_review"
_ROUTE_GOVERNANCE_REVIEW = [
    "wow.governed-red-team-reviewer",
    "wow.qa-hallucination-auditor",
]
_ROUTE_KALSHI_SPORTS = [
    "wow.kalshi-contract-intelligence",  # health first — stops on INVENTORY_EMPTY
    "wow.sports-research-analyst",
    "wow.market-odds-intelligence",
    "wow.probability-ev-auditor",
    "wow.kalshi-contract-intelligence",  # second pass: fill/orderbook
    "wow.bankroll-risk-manager",
    "wow.qa-hallucination-auditor",
]
_ROUTE_KALSHI_WEATHER = [
    "wow.weather-intelligence",
    "wow.probability-ev-auditor",
    "wow.kalshi-contract-intelligence",
    "wow.qa-hallucination-auditor",
]
_ROUTE_LOTTERY = [
    "wow.lottery-analyst",
    "wow.qa-hallucination-auditor",
]


def _db_persist(run_id: str, run_record: dict) -> None:
    """Persist skills run to DB.  Best-effort — never throws."""
    try:
        import psycopg2
        import psycopg2.extras
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            return
        conn = psycopg2.connect(url)
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS skills_run_log (
                        id           SERIAL PRIMARY KEY,
                        run_id       TEXT NOT NULL,
                        created_at   TIMESTAMPTZ DEFAULT NOW(),
                        market_type  TEXT,
                        final_label  TEXT,
                        skill_count  INT,
                        blockers     JSONB,
                        run_record   JSONB
                    )""")
                cur.execute("""
                    INSERT INTO skills_run_log
                        (run_id, market_type, final_label, skill_count, blockers, run_record)
                    VALUES (%s, %s, %s, %s, %s, %s)""",
                    (run_id,
                     run_record.get("market_type"),
                     run_record.get("final_label"),
                     run_record.get("skill_count", 0),
                     json.dumps(run_record.get("blockers", [])),
                     json.dumps(run_record)))
        conn.close()
    except Exception:
        pass  # persistence is best-effort


class SkillOrchestrator:
    """
    Runs the WOW v16 skill sequence for a given market context.

    Key invariants enforced here (ORCHESTRATOR.md §Final decision ownership):
      - Lowest-ceiling propagation: a downstream skill CANNOT erase an upstream blocker.
      - Kalshi sports stops immediately on INVENTORY_EMPTY health signal.
      - Four+ Kalshi market combo hard-rejects during Reliability Freeze.
      - can_execute is always False (enforced in SkillResult.__post_init__).
    """

    def __init__(self, registry: SkillRegistry | None = None) -> None:
        self._registry = registry or SkillRegistry.get()
        self._adapter_map: dict | None = None

    def _adapters(self) -> dict:
        if self._adapter_map is None:
            from .adapters import ADAPTER_MAP
            self._adapter_map = ADAPTER_MAP
        return self._adapter_map

    def _get_adapter(self, skill_id: str):
        adapters = self._adapters()
        cls = adapters.get(skill_id)
        if cls is None:
            return None
        return cls()

    # ── Routing ────────────────────────────────────────────────────────────────

    def _pick_route(self, context: dict) -> tuple[str, list[str]]:
        """Return (market_type, ordered_skill_ids) for the given context."""
        market_type = context.get("market_type", "player_prop")
        if market_type == "lottery":
            return "lottery", _ROUTE_LOTTERY
        if market_type == "kalshi_weather":
            return "kalshi_weather", _ROUTE_KALSHI_WEATHER
        if market_type == "kalshi_sports":
            return "kalshi_sports", _ROUTE_KALSHI_SPORTS
        if market_type == "governance_review":
            return "governance_review", _ROUTE_GOVERNANCE_REVIEW
        if market_type in ("team_winner", "team_total", "team_spread"):
            return market_type, _ROUTE_SPORTS_TEAM
        return "player_prop", _ROUTE_PLAYER_PROP

    # ── Reliability Freeze checks ──────────────────────────────────────────────

    def _check_reliability_freeze(self, context: dict, market_type: str) -> dict | None:
        """
        Acceptance test 18: four-market Kalshi sports combo hard-rejects during
        Reliability Freeze.
        Returns a blocker dict if the run must be stopped, else None.
        """
        if not context.get("reliability_freeze"):
            return None
        if market_type != "kalshi_sports":
            return None
        combo_markets = context.get("kalshi_combo_markets", [])
        if len(combo_markets) >= 4:
            return {
                "code": "RELIABILITY_FREEZE_COMBO_HARD_REJECT",
                "message": (f"Four-market Kalshi sports combo hard-rejected during "
                            f"Reliability Freeze ({len(combo_markets)} markets)."),
                "fatal": True,
            }
        return None

    # ── Main run method ────────────────────────────────────────────────────────

    def run(self, context: dict) -> dict:
        """
        Execute the full skill sequence for the given context.

        Args:
            context: dict with keys such as:
              market_type, event_id, market_id, reliability_freeze,
              kalshi_inventory_health, kalshi_combo_markets, inputs, ...

        Returns:
            Orchestration result dict with:
              run_id, final_label, skill_results, blockers, can_execute=False,
              stopped_early (bool), stop_reason (str | None)
        """
        run_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc).isoformat()

        market_type, route = self._pick_route(context)

        # Reliability Freeze hard-reject gate
        freeze_blocker = self._check_reliability_freeze(context, market_type)
        if freeze_blocker:
            result = {
                "run_id":       run_id,
                "started_at":   started_at,
                "market_type":  market_type,
                "final_label":  SkillLabel.REJECT_BAD_RULES.value,
                "skill_results": [],
                "blockers":     [freeze_blocker],
                "can_execute":  False,
                "stopped_early": True,
                "stop_reason":  freeze_blocker["code"],
                "skill_count":  0,
            }
            _db_persist(run_id, result)
            return result

        skill_results: list[dict] = []
        ceiling = SkillLabel.READY.value
        all_blockers: list[dict] = []
        stopped_early = False
        stop_reason: str | None = None

        # Deduplicate route while preserving order (kalshi might appear twice)
        seen_ids: set[str] = set()
        deduped_route = []
        for sid in route:
            if sid not in seen_ids:
                deduped_route.append(sid)
                seen_ids.add(sid)

        for skill_id in deduped_route:
            # Kalshi sports: stop immediately on INVENTORY_EMPTY
            if market_type == "kalshi_sports" and skill_id == "wow.kalshi-contract-intelligence":
                health = context.get("kalshi_inventory_health", "")
                if health == KALSHI_SPORTS_STOP_SIGNAL:
                    stop_blocker = {
                        "code": "KALSHI_INVENTORY_EMPTY",
                        "message": "Kalshi sports inventory is EMPTY; stopping scan.",
                        "fatal": True,
                    }
                    all_blockers.append(stop_blocker)
                    ceiling = lower_ceiling(ceiling, SkillLabel.DATA_UNOBTAINABLE.value)
                    stopped_early = True
                    stop_reason = "KALSHI_INVENTORY_EMPTY"
                    break

            adapter = self._get_adapter(skill_id)
            if adapter is None:
                # Unknown adapter — skip but do not fail the run
                continue

            try:
                skill_result = adapter.run(context, run_id=run_id)
            except Exception as exc:
                skill_result_dict = {
                    "skill_id":    skill_id,
                    "label":       SkillLabel.DATA_UNOBTAINABLE.value,
                    "blockers":    [{"code": "ADAPTER_ERROR",
                                     "message": str(exc), "fatal": True}],
                    "can_execute": False,
                }
                skill_results.append(skill_result_dict)
                ceiling = lower_ceiling(ceiling, SkillLabel.DATA_UNOBTAINABLE.value)
                all_blockers.append(skill_result_dict["blockers"][0])
                continue

            d = skill_result.to_dict()
            skill_results.append(d)

            # Lowest-ceiling propagation
            new_ceiling = lower_ceiling(ceiling, d["label"])
            ceiling = new_ceiling

            # Collect fatal blockers
            for b in d.get("blockers", []):
                if b.get("fatal"):
                    all_blockers.append(b)

        result = {
            "run_id":        run_id,
            "started_at":    started_at,
            "market_type":   market_type,
            "final_label":   ceiling,
            "skill_results": skill_results,
            "blockers":      all_blockers,
            "can_execute":   False,          # INVARIANT: always False at orchestrator level
            "stopped_early": stopped_early,
            "stop_reason":   stop_reason,
            "skill_count":   len(skill_results),
        }
        _db_persist(run_id, result)
        return result
