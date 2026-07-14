"""
l5_l10_ledger.py
Calculate L5/L10 average, median, hit rate, season average, and splits.

WOW-PATCH-MANDATORY-RECONSTRUCTION-v1.0:
  When game_log is absent, attempt reconstruction from season_log before
  returning a failure status. Documents all source-attempt steps as required
  by the Mandatory Data Acquisition and Reconstruction patch.
"""
from __future__ import annotations

import statistics
from typing import Any

from .labels import DataStatus
from .acquisition import SourceStatus, ReconstructionStatus


MIN_GAMES_L5  = 4
MIN_GAMES_L10 = 6


def run(row: dict[str, Any], game_log: list[float] | None = None,
        season_log: list[float] | None = None) -> dict[str, Any]:
    """
    game_log    — chronological list of stat values (most recent last)
    season_log  — full season log (superset of game_log, or None)

    Gate result at row["gates"]["l5_l10_ledger"].

    Acquisition tracking:
      l5_source_status        — SourceStatus for L5 window
      l10_source_status       — SourceStatus for L10 window
      reconstruction_method   — how data was obtained
      source_rows_used        — number of source rows consumed
      reconstruction_confidence — RECONSTRUCTED_A / _B_* / _FAILED
      l5_line_used            — the line used for L5 hit rate
    """
    line = row.get("line")

    # -----------------------------------------------------------------------
    # Primary source: game_log (direct feed)
    # -----------------------------------------------------------------------
    _source_attempts: list[dict[str, str]] = []

    if game_log is None:
        # Record that the primary feed was not provided
        _source_attempts.append({
            "source": "direct_game_log_feed",
            "status": SourceStatus.NOT_CALLED,
            "detail": "game_log not supplied by caller",
        })

        # -------------------------------------------------------------------
        # Acquisition Ladder Step: attempt reconstruction from season_log
        # -------------------------------------------------------------------
        if season_log is not None:
            clean_s = [float(v) for v in season_log if v is not None]
            if len(clean_s) >= MIN_GAMES_L5:
                _source_attempts.append({
                    "source": "season_log_reconstruction",
                    "status": SourceStatus.RECONSTRUCTED,
                    "detail": (
                        f"{len(clean_s)} season-log rows used as game_log proxy; "
                        "corroboration source: none (single feed)"
                    ),
                })
                # Treat season_log as game_log for scoring, flag as B-uncorroborated
                # because we have only one source (no secondary corroboration)
                recon_status = (
                    ReconstructionStatus.RECONSTRUCTED_A
                    if len(clean_s) >= 10
                    else ReconstructionStatus.RECONSTRUCTED_B_UNCORROBORATED
                )
                result = _compute(clean_s, line, row.get("direction", "MORE"),
                                  season_log=None)
                result.update({
                    "l5_source_status":         SourceStatus.RECONSTRUCTED,
                    "l10_source_status":        SourceStatus.RECONSTRUCTED,
                    "reconstruction_method":    "season_log_reconstruction",
                    "source_rows_used":         len(clean_s),
                    "reconstruction_confidence": recon_status,
                    "l5_line_used":             line,
                    "source_attempts":          _source_attempts,
                    "approval_cap": (
                        "MODEL_QUALIFIED_HOLD"
                        if recon_status == ReconstructionStatus.RECONSTRUCTED_B_UNCORROBORATED
                        else None
                    ),
                })
                row["gates"]["l5_l10_ledger"] = result
                # Approval cap blocker for RECONSTRUCTED_B_UNCORROBORATED
                if recon_status == ReconstructionStatus.RECONSTRUCTED_B_UNCORROBORATED:
                    row["blockers"].append(
                        "L10:RECONSTRUCTED_B_UNCORROBORATED:"
                        "MAX_LABEL=MODEL_QUALIFIED_HOLD"
                    )
                return row
            else:
                _source_attempts.append({
                    "source": "season_log_reconstruction",
                    "status": SourceStatus.FAILED,
                    "detail": (
                        f"season_log has {len(clean_s)} valid rows, "
                        f"minimum {MIN_GAMES_L5} required"
                    ),
                })
        else:
            _source_attempts.append({
                "source": "season_log_reconstruction",
                "status": SourceStatus.NOT_CALLED,
                "detail": "season_log not supplied by caller",
            })

        # All acquisition paths exhausted
        result = _no_data_result("NO_GAME_LOG_PROVIDED")
        result.update({
            "l5_source_status":         SourceStatus.DATA_UNOBTAINABLE,
            "l10_source_status":        SourceStatus.DATA_UNOBTAINABLE,
            "reconstruction_method":    "none",
            "source_rows_used":         0,
            "reconstruction_confidence": ReconstructionStatus.RECONSTRUCTION_FAILED,
            "l5_line_used":             line,
            "source_attempts":          _source_attempts,
            "approval_cap":             None,
        })
        row["gates"]["l5_l10_ledger"] = result
        row["blockers"].append("L10:NO_GAME_LOG_PROVIDED")
        return row

    # -----------------------------------------------------------------------
    # game_log provided — primary source path
    # -----------------------------------------------------------------------
    _source_attempts.append({
        "source": "direct_game_log_feed",
        "status": SourceStatus.RETRIEVED,
        "detail": f"{len(game_log)} rows supplied",
    })

    clean = [float(v) for v in game_log if v is not None]

    if len(clean) < MIN_GAMES_L5:
        result = _no_data_result(
            f"INSUFFICIENT_GAMES:{len(clean)}_of_{MIN_GAMES_L5}_required"
        )
        result.update({
            "l5_source_status":         SourceStatus.FAILED,
            "l10_source_status":        SourceStatus.FAILED,
            "reconstruction_method":    "direct_game_log",
            "source_rows_used":         len(clean),
            "reconstruction_confidence": ReconstructionStatus.RECONSTRUCTION_FAILED,
            "l5_line_used":             line,
            "source_attempts":          _source_attempts,
            "approval_cap":             None,
        })
        row["gates"]["l5_l10_ledger"] = result
        row["blockers"].append(f"L10:SMALL_SAMPLE:{len(clean)}")
        return row

    result = _compute(clean, line, row.get("direction", "MORE"),
                      season_log=season_log)

    has_l10 = len(clean) >= 10
    l_status = DataStatus.RETRIEVED.value if has_l10 else DataStatus.RECONSTRUCTED.value
    src_status = SourceStatus.RETRIEVED if has_l10 else SourceStatus.RECONSTRUCTED
    recon = (
        ReconstructionStatus.RECONSTRUCTED_A if has_l10
        else ReconstructionStatus.RECONSTRUCTED_B_UNCORROBORATED
    )
    result.update({
        "data_status":              l_status,
        "l5_source_status":         src_status,
        "l10_source_status":        src_status,
        "reconstruction_method":    "direct_game_log",
        "source_rows_used":         len(clean),
        "reconstruction_confidence": recon,
        "l5_line_used":             line,
        "source_attempts":          _source_attempts,
        "approval_cap": (
            "MODEL_QUALIFIED_HOLD"
            if recon == ReconstructionStatus.RECONSTRUCTED_B_UNCORROBORATED
            else None
        ),
    })
    row["gates"]["l5_l10_ledger"] = result
    return row


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _compute(
    clean: list[float],
    line: float | None,
    direction: str,
    season_log: list[float] | None = None,
) -> dict[str, Any]:
    """Compute all L5/L10 metrics from a clean game list."""
    l5  = clean[-5:] if len(clean) >= 5 else clean
    l10 = clean[-10:] if len(clean) >= 10 else clean

    l5_avg    = round(statistics.mean(l5), 2)
    l10_avg   = round(statistics.mean(l10), 2)
    l5_median = round(statistics.median(l5), 2)
    l10_median= round(statistics.median(l10), 2)

    season_avg = None
    if season_log:
        season_clean = [float(v) for v in season_log if v is not None]
        if season_clean:
            season_avg = round(statistics.mean(season_clean), 2)

    l5_hit_rate  = _hit_rate(l5,  line, direction)
    l10_hit_rate = _hit_rate(l10, line, direction)

    has_l10 = len(clean) >= 10

    return {
        "passed":               True,
        "data_status":          (
            DataStatus.RETRIEVED.value if has_l10
            else DataStatus.RECONSTRUCTED.value
        ),
        "games_available":      len(clean),
        "l5_avg":               l5_avg,
        "l5_median":            l5_median,
        "l5_hit_rate":          l5_hit_rate,
        "l10_avg":              l10_avg,
        "l10_median":           l10_median,
        "l10_hit_rate":         l10_hit_rate,
        "season_avg":           season_avg,
        "l5_games":             l5,
        "l10_games":            l10,
        "small_sample_warning": not has_l10,
    }


def _hit_rate(games: list[float], line: float | None, direction: str) -> float | None:
    if line is None or not games:
        return None
    direction = direction.upper()
    hits = sum(
        1 for g in games
        if (direction in ("MORE", "OVER") and g > line)
        or (direction in ("LESS", "UNDER") and g < line)
    )
    return round(hits / len(games), 3)


def _no_data_result(reason: str) -> dict[str, Any]:
    return {
        "passed":               False,
        "data_status":          DataStatus.FAILED.value,
        "reason":               reason,
        "games_available":      0,
        "l5_avg":               None,
        "l5_median":            None,
        "l5_hit_rate":          None,
        "l10_avg":              None,
        "l10_median":           None,
        "l10_hit_rate":         None,
        "season_avg":           None,
        "l5_games":             [],
        "l10_games":            [],
        "small_sample_warning": True,
    }
