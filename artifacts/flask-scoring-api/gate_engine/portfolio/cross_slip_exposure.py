"""
gate_engine/portfolio/cross_slip_exposure.py  —  PATCH-PORTFOLIO-001
Cross-Slip Exposure Governor

Enforces session-level thesis and market-family deduplication.
Complementary to PgSessionLedger (which handles player/game/archetype dedup).

This governor enforces two additional hard rules:
  1. market-family dedup (player + stat_family, any line/direction): max 1 per session
     → Catches alternate-line exposure: PRA 19.5 and PRA 22.5 on the same player
       are the same underlying distribution — the second is blocked.
  2. thesis dedup (player + stat_family + direction): max 1 per session
     → Catches exact-duplicate directional bets.

Stage 1: in-memory tracking within a single run_pipeline() call.
Stage 2: promote to DB-backed cross-request persistence (same pattern as pg_session_ledger).

Hard rules (configurable):
  MAX_MKTFAMILY = 1  — one prop per (player, stat_family) per session
  MAX_THESIS    = 1  — one identical thesis per session

can_execute=False is unconditional.
"""
from __future__ import annotations

from typing import Any

can_execute = False

# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

MAX_MKTFAMILY: int = 1   # max props with same (player, stat_family)
MAX_THESIS:    int = 1   # max identical (player, stat_family, direction)

# ---------------------------------------------------------------------------
# Blocker / label constants (PATCH-PORTFOLIO-001)
# ---------------------------------------------------------------------------

LABEL_DUPLICATE_PLAYER    = "REJECT_DUPLICATE_PLAYER_EXPOSURE"
LABEL_DUPLICATE_THESIS    = "REJECT_DUPLICATE_THESIS"
LABEL_CROSS_SLIP_CONC     = "REJECT_CROSS_SLIP_CONCENTRATION"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    return (s or "").lower().strip().replace("+", " ").replace("-", " ").replace("_", " ")


def _make_keys(row: dict[str, Any]) -> tuple[str, str]:
    """Return (mktfamily_key, thesis_key) for a row."""
    player    = _norm(row.get("player") or row.get("player_name") or "UNKNOWN")
    stat      = _norm(
        row.get("prop_type") or row.get("prop") or
        row.get("stat_type") or row.get("stat_family") or ""
    )
    direction = (row.get("direction") or row.get("side") or "").upper().strip()

    mktfamily_key = f"{player}|{stat}"
    thesis_key    = f"{player}|{stat}|{direction}"
    return mktfamily_key, thesis_key


# ---------------------------------------------------------------------------
# Governor class
# ---------------------------------------------------------------------------

class PortfolioExposureGovernor:
    """
    Stage 1: in-memory cross-slip exposure governor.

    Create one instance per run_pipeline() call (or per session for Stage 2).
    Pass to pipeline.run_pipeline() via the portfolio_governor parameter.

    Interface mirrors PgSessionLedger.check_and_register(row) so the pipeline
    can swap between in-memory and DB-backed governors transparently.
    """

    def __init__(
        self,
        session_id: str = "",
        conn_string: str | None = None,  # reserved for Stage 2 DB-backed path
        max_mktfamily: int = MAX_MKTFAMILY,
        max_thesis:    int = MAX_THESIS,
    ) -> None:
        self.session_id    = session_id
        self.max_mktfamily = max_mktfamily
        self.max_thesis    = max_thesis

        # In-memory tracking sets
        self._mktfamily_counts: dict[str, int] = {}
        self._thesis_counts:    dict[str, int] = {}

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def check_and_register(self, row: dict[str, Any]) -> dict[str, Any]:
        """
        Check exposure limits and register the row if it passes.

        Stamps row["gates"]["portfolio_exposure"] with the result.
        On block: extends row["blockers"] and sets row["terminal_label"].
        """
        row.setdefault("gates",    {})
        row.setdefault("blockers", [])
        row["can_execute"] = False

        mktfamily_key, thesis_key = _make_keys(row)

        mktf_count   = self._mktfamily_counts.get(mktfamily_key, 0)
        thesis_count = self._thesis_counts.get(thesis_key, 0)

        blocks: list[str] = []

        # Market-family check first (catches alternate-line exposure)
        if mktf_count >= self.max_mktfamily:
            blocks.append(
                f"{LABEL_CROSS_SLIP_CONC}"
                f":alternate_line_or_same_distribution:{mktfamily_key}"
                f":{mktf_count + 1}x"
            )
        elif thesis_count >= self.max_thesis:
            blocks.append(
                f"{LABEL_DUPLICATE_THESIS}:{thesis_key}:{thesis_count + 1}x"
            )

        passed = len(blocks) == 0

        row["gates"]["portfolio_exposure"] = {
            "passed":         passed,
            "blocks":         blocks,
            "mktfamily_key":  mktfamily_key,
            "thesis_key":     thesis_key,
            "mktfamily_count": mktf_count,
            "thesis_count":   thesis_count,
            "session_id":     self.session_id,
            "backend":        "memory",
            "can_execute":    False,
        }

        if not passed:
            row["blockers"].extend(blocks)
            # Assign terminal label — most restrictive blocker wins
            if any(LABEL_CROSS_SLIP_CONC in b for b in blocks):
                row["terminal_label"] = LABEL_CROSS_SLIP_CONC
            else:
                row["terminal_label"] = LABEL_DUPLICATE_THESIS
        else:
            # Register both keys
            self._mktfamily_counts[mktfamily_key] = mktf_count + 1
            self._thesis_counts[thesis_key]       = thesis_count + 1

        return row

    def snapshot(self) -> dict[str, Any]:
        """Return current in-memory exposure state."""
        return {
            "session_id":      self.session_id,
            "backend":         "memory",
            "mktfamily_seen":  dict(self._mktfamily_counts),
            "thesis_seen":     dict(self._thesis_counts),
            "max_mktfamily":   self.max_mktfamily,
            "max_thesis":      self.max_thesis,
            "can_execute":     False,
        }


# ---------------------------------------------------------------------------
# Convenience factory (used in app.py gate_engine_run)
# ---------------------------------------------------------------------------

def make_portfolio_governor(
    session_id: str = "",
    conn_string: str | None = None,
) -> PortfolioExposureGovernor:
    """Create a PortfolioExposureGovernor for the given session."""
    return PortfolioExposureGovernor(
        session_id=session_id,
        conn_string=conn_string,
    )
