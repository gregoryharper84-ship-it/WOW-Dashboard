"""Research-only replay helpers for future JS selection-style imitation work.

The target is `selected_by_js`; sporting outcome is deliberately excluded from
imitation inputs and no fitted sporting probability is produced here.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Iterable

from v17.js_style.contracts import CandidateObservation


def build_selection_style_rows(observations: Iterable[CandidateObservation]) -> tuple[dict, ...]:
    rows: list[dict] = []
    labels: set[bool] = set()
    for observation in observations:
        if observation.selected_by_js is None:
            continue
        labels.add(bool(observation.selected_by_js))
        payload = asdict(observation)
        # Outcome/settlement can exist in the learning ledger for evaluation, but
        # may never enter the imitation model's feature row.
        payload.pop("eventual_settlement", None)
        payload.pop("governed_scoring_status", None)
        payload.pop("governed_typed_blocker", None)
        payload["target_selected_by_js"] = bool(observation.selected_by_js)
        payload["research_only"] = True
        payload["js_probability_authority"] = False
        payload["probability_publishable"] = False
        payload["rank_eligible"] = False
        payload["can_execute"] = False
        rows.append(payload)

    if rows and labels != {False, True}:
        raise ValueError("JS_IMITATION_CLASS_SUPPORT_INSUFFICIENT")
    return tuple(rows)


__all__ = ["build_selection_style_rows"]
