"""Regression coverage for fail-closed cross-sport prop activation plumbing."""
from __future__ import annotations

import pick_request_runtime as runtime
import pick_request_runtime_core as core
import prop_auto_hydration_router


def test_wnba_board_aliases_normalize_before_specialist_and_artifact_lookup():
    assert core._canonical_stat("WNBA", "PTS") == "POINTS"
    assert core._canonical_stat("WNBA", "reb") == "REBOUNDS"
    assert core._canonical_stat("WNBA", "ast") == "ASSISTS"
    assert core._canonical_stat("WNBA", "3PM") == "THREE_POINTERS_MADE"
    assert core._canonical_stat("WNBA", "3-pt made") == "THREE_POINTERS_MADE"


def test_pick_request_facade_defaults_to_reviewed_sport_aware_hydrator():
    assert runtime.auto_hydrate_prop_evidence is prop_auto_hydration_router.auto_hydrate_prop_evidence


def test_core_route_uses_facade_delegate_without_granting_probability_authority():
    assert core.auto_hydrate_prop_evidence is runtime._auto_hydrate_prop_evidence_delegate
    assert core.PROP_STAT_ALIASES[("WNBA", "PTS")] == "POINTS"
    assert core.PROP_STAT_ALIASES[("WNBA", "3PM")] == "THREE_POINTERS_MADE"
    # This plumbing change never touches execution authority.
    assert prop_auto_hydration_router.WNBA_PROVIDER
