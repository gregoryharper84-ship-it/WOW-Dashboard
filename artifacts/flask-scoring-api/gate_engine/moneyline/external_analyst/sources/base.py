"""
gate_engine/moneyline/external_analyst/sources/base.py
WOW-PATCH-2026-08-08-EXTERNAL-ANALYST-INTELLIGENCE

Abstract base class for all external analyst source adapters.

All implementations must:
  - Return a list of AnalystOpinion objects (empty on failure)
  - Set source_status on each opinion (RETRIEVED, DATA_UNOBTAINABLE, etc.)
  - Never raise — failures must return empty list with status logged
  - Never mutate failure_path_matrix or sport model probability
  - Keep direct_probability_weight = 0.0 on every opinion

can_execute=False unconditional.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from gate_engine.moneyline.external_analyst.types import AnalystOpinion

can_execute: bool = False  # UNCONDITIONAL


class ExternalAnalystSourceBase(ABC):
    """
    Abstract base for all external analyst adapters.

    Subclasses implement fetch() to retrieve picks for a specific
    sport/event from their source (HTTP, enrichment dict, etc.).

    source_family and source_name must be set by the subclass.
    """

    source_name:   str = ""
    source_family: str = ""

    @abstractmethod
    def fetch(
        self,
        sport:      str,
        team:       str,
        opponent:   str,
        event_date: str | None,
        enrichment: dict[str, Any] | None = None,
    ) -> list[AnalystOpinion]:
        """
        Retrieve analyst picks for the given matchup.

        Parameters
        ----------
        sport       : e.g. "MLB", "NBA"
        team        : The candidate team name
        opponent    : The opponent team name
        event_date  : ISO date string "YYYY-MM-DD" or None
        enrichment  : The full enrichment dict (may contain pre-supplied picks)

        Returns a list of AnalystOpinion objects.
        Returns [] on any failure — must never raise.
        """
        ...

    def _unobtainable_opinion(
        self,
        sport:      str | None,
        team:       str | None,
        opponent:   str | None,
        event_date: str | None,
        reason:     str,
    ) -> list[AnalystOpinion]:
        """Return a single DATA_UNOBTAINABLE opinion for observability."""
        from gate_engine.moneyline.external_analyst.types import (
            AnalystSourceStatus, ThesisTags
        )
        op = AnalystOpinion(
            source_name   = self.source_name,
            source_family = self.source_family,
            sport         = sport,
            team          = team,
            opponent      = opponent,
            event_date    = event_date,
            source_status = AnalystSourceStatus.DATA_UNOBTAINABLE,
            acquisition_notes = [reason],
        )
        return [op]
