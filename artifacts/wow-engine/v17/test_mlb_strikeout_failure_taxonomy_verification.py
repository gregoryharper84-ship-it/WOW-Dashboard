"""Independent verification of V17 failure-taxonomy correctness for the MLB
pitcher-strikeouts vertical specifically (Gap #5 from the strikeouts audit).

The audit found the four canonical labels (MODEL_UNAVAILABLE /
MODEL_SCORER_FAILED / MODEL_INPUTS_INSUFFICIENT / MODEL_OUTPUT_INVALID)
correctly defined and reduced by a *shared* cross-sport mechanism, but not
independently re-verified end to end for this prop. This module traces the
actual codes the strikeouts adapter/hydration raise through the two real
classification surfaces that exist in this codebase and confirms neither
collapses a scorer/input/output failure into MODEL_UNAVAILABLE for this prop.

Two classification surfaces exist by design, not by accident:
  1. ``prop_terminal_reducer_v2.reduce_prop_terminal`` -- reduces a row that
     reached model evaluation (or a documented pre-model rejection) into one
     terminal label plus blocker set.
  2. ``calibration_publication_api._OUTPUT_INVALID_CODES`` /
     ``_CAPABILITY_ABSENCE_CODES`` -- classifies a raw adapter/provider
     exception code (e.g. from ``mlb_pitcher_so_failure_path_nb_v1_adapter``)
     for forward-shadow publication scoping, before any row exists to reduce.

This file only reads/exercises existing code; it does not change any
classification behavior.
"""
from __future__ import annotations

import unittest

from calibration_publication_api import _CAPABILITY_ABSENCE_CODES, _OUTPUT_INVALID_CODES
from prop_terminal_reducer_v2 import (
    INPUT_BLOCKERS,
    MODEL_CAPABILITY_BLOCKERS,
    OUTPUT_INVALID_BLOCKERS,
    SCORER_FAILURE_BLOCKERS,
    reduce_prop_terminal,
)

# Actual codes raised by strikeouts-specific code paths, held here as
# hardcoded string literals (not re-derived) so a future edit to either
# source module that quietly changes a code string breaks this test loudly
# rather than the check silently degrading to a no-op.
STRIKEOUT_ARTIFACT_PAYLOAD_INVALID_CODE = "PROP_MODEL_ARTIFACT_PAYLOAD_INVALID"  # prop_model_adapters.py:202
STRIKEOUT_EVIDENCE_FEATURE_MISALIGNED_CODE = "PROP_EVIDENCE_FEATURE_MISALIGNED"  # prop_model_adapters.py:210
STRIKEOUT_RECENT_STARTS_INSUFFICIENT_CODE = "MLB_RECENT_STARTS_INSUFFICIENT"  # prop_auto_hydration.py


class ModelUnavailableCategoryTest(unittest.TestCase):
    """An absent controlling capability for strikeouts must reduce to
    MODEL_UNAVAILABLE, and only via MODEL_CAPABILITY_BLOCKERS -- never via a
    scorer/input/output blocker string that merely happens to look similar."""

    def test_missing_certified_artifact_reduces_to_model_unavailable(self):
        decision = reduce_prop_terminal(
            proposed_label="MODEL_UNAVAILABLE",
            blockers=["PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND"],
            model_evaluated=False,
        )
        self.assertEqual(decision.terminal_label, "MODEL_UNAVAILABLE")
        self.assertIn("PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND", MODEL_CAPABILITY_BLOCKERS)

    def test_capability_absence_publication_scoping_agrees(self):
        self.assertIn("PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND", _CAPABILITY_ABSENCE_CODES)
        # The adapter-payload-invalid code must NOT also appear as a
        # capability-absence code -- a malformed artifact is not the same
        # failure as no artifact at all.
        self.assertNotIn(STRIKEOUT_ARTIFACT_PAYLOAD_INVALID_CODE, _CAPABILITY_ABSENCE_CODES)


class ScorerFailedCategoryTest(unittest.TestCase):
    """A strikeout scorer exception must reduce to MODEL_SCORER_FAILED, never
    to MODEL_UNAVAILABLE (the model *was* selected and invoked; it failed)."""

    def test_scorer_exception_during_strikeout_scoring_reduces_to_scorer_failed(self):
        decision = reduce_prop_terminal(
            proposed_label="MODEL_SCORER_FAILED",
            blockers=["PROP_SCORER_EXCEPTION"],
            model_evaluated=False,
        )
        self.assertEqual(decision.terminal_label, "MODEL_SCORER_FAILED")
        self.assertNotEqual(decision.terminal_label, "MODEL_UNAVAILABLE")
        self.assertIn("PROP_SCORER_EXCEPTION", SCORER_FAILURE_BLOCKERS)


class ModelInputsInsufficientCategoryTest(unittest.TestCase):
    """The strikeout-specific 'not enough recent starts' hydration blocker
    must reduce to MODEL_INPUTS_INSUFFICIENT when the model never ran, not to
    MODEL_UNAVAILABLE or a bare rejection label."""

    def test_recent_starts_insufficient_reduces_to_model_inputs_insufficient(self):
        self.assertIn(STRIKEOUT_RECENT_STARTS_INSUFFICIENT_CODE, INPUT_BLOCKERS)
        decision = reduce_prop_terminal(
            proposed_label="NO_PLAY",
            blockers=[STRIKEOUT_RECENT_STARTS_INSUFFICIENT_CODE],
            model_evaluated=False,
        )
        self.assertEqual(decision.terminal_label, "MODEL_INPUTS_INSUFFICIENT")

    def test_same_blocker_does_not_suppress_a_real_model_evaluation(self):
        # If the model *did* evaluate despite this blocker being present in
        # history (e.g. concurrent but non-causal), input-insufficiency must
        # not be reported as the terminal cause.
        decision = reduce_prop_terminal(
            proposed_label="REJECT_PROBABILITY",
            blockers=[STRIKEOUT_RECENT_STARTS_INSUFFICIENT_CODE],
            model_evaluated=True,
        )
        self.assertNotEqual(decision.terminal_label, "MODEL_INPUTS_INSUFFICIENT")


class ModelOutputInvalidCategoryTest(unittest.TestCase):
    """A malformed strikeout artifact payload or misaligned feature history
    is an output/contract failure, not an input-availability or capability
    failure, and the two real classification surfaces must agree on that."""

    def test_publication_scoping_classifies_artifact_payload_invalid_as_output_invalid(self):
        self.assertIn(STRIKEOUT_ARTIFACT_PAYLOAD_INVALID_CODE, _OUTPUT_INVALID_CODES)

    def test_terminal_reducer_output_invalid_blockers_do_not_include_input_blockers(self):
        # Disjointness check: a code cannot be simultaneously treated as an
        # input deficiency and an output/contract defect by the reducer,
        # since that would make classification order-dependent/ambiguous.
        self.assertFalse(OUTPUT_INVALID_BLOCKERS & INPUT_BLOCKERS)

    def test_calibrated_bound_missing_reduces_to_model_output_invalid(self):
        decision = reduce_prop_terminal(
            proposed_label="MODEL_OUTPUT_INVALID",
            blockers=["CALIBRATED_PROBABILITY_OR_BOUND_MISSING"],
            model_evaluated=True,
        )
        self.assertEqual(decision.terminal_label, "MODEL_OUTPUT_INVALID")


class NoCategoryCollapsesIntoAnotherTest(unittest.TestCase):
    """The four canonical blocker sets used by the shared reducer must stay
    mutually exclusive; if any pair intersects, one blocker string could
    reduce to two different terminal labels depending on set iteration
    order, silently collapsing categories for whichever prop hits it --
    including strikeouts."""

    def test_all_four_canonical_blocker_sets_are_pairwise_disjoint(self):
        sets = {
            "MODEL_UNAVAILABLE": MODEL_CAPABILITY_BLOCKERS,
            "MODEL_SCORER_FAILED": SCORER_FAILURE_BLOCKERS,
            "MODEL_INPUTS_INSUFFICIENT": INPUT_BLOCKERS,
            "MODEL_OUTPUT_INVALID": OUTPUT_INVALID_BLOCKERS,
        }
        names = list(sets)
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = names[i], names[j]
                overlap = sets[a] & sets[b]
                self.assertFalse(overlap, f"{a} and {b} share blocker codes: {overlap}")

    def test_strikeout_specific_blockers_each_land_in_exactly_one_set(self):
        strikeout_blockers = {
            STRIKEOUT_RECENT_STARTS_INSUFFICIENT_CODE: INPUT_BLOCKERS,
        }
        all_sets = [MODEL_CAPABILITY_BLOCKERS, SCORER_FAILURE_BLOCKERS, INPUT_BLOCKERS, OUTPUT_INVALID_BLOCKERS]
        for code, expected_set in strikeout_blockers.items():
            membership_count = sum(1 for s in all_sets if code in s)
            self.assertEqual(membership_count, 1, f"{code} must belong to exactly one canonical set")
            self.assertIn(code, expected_set)


if __name__ == "__main__":
    unittest.main()
