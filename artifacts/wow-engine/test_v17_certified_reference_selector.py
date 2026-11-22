"""Offline adversarial tests for the source-only V17 reference selector."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from v17.certified_reference_selector import (
    ExactCandidate, ModelResult, ReferenceRegistration, choose_reference_route,
    CAN_EXECUTE, TERMINAL_AUTHORITY,
)

NOW = datetime(2026, 10, 19, 18, 0, tzinfo=timezone.utc)

def candidate(**kw):
    fields = dict(
        candidate_id="nfl:event-1:player:yards:38.5:MORE",
        sport="NFL", market_family="PLAYER_PROP", stat_family="RUSHING_YARDS",
        period="FULL_GAME", official_event_id="event-1", settlement_id="yards-fullgame",
        event_status="PREGAME", latest_material_update_at=NOW-timedelta(minutes=5),
        as_of=NOW, exact_line=38.5, direction="MORE",
    )
    fields.update(kw)
    return ExactCandidate(**fields)

def failed(c=None, status="MODEL_SCORER_FAILED", **kw):
    c = c or candidate()
    return ModelResult(status, c.candidate_id, model_id="primary_v17", **kw)

def registration(**kw):
    fields = dict(
        model_id="reference_v1", artifact_id="artifact-reference-1",
        calibrator_id="calibrator-reference-1", certification_id="cert-123",
        independent_review_id="sirt-review-456", active=True, certified=True,
        sport="NFL", market_family="PLAYER_PROP", stat_family="RUSHING_YARDS",
        period="FULL_GAME", settlement_id="yards-fullgame", exact_lines=(38.5,),
        allowed_primary_failures=("MODEL_UNAVAILABLE", "MODEL_SCORER_FAILED",
                                  "MODEL_OUTPUT_INVALID", "MODEL_INPUTS_INSUFFICIENT"),
        valid_from=NOW-timedelta(days=1), valid_until=NOW+timedelta(days=1),
    )
    fields.update(kw)
    return ReferenceRegistration(**fields)

def result(c=None, **kw):
    c = c or candidate()
    fields = dict(
        status="VALID", candidate_id=c.candidate_id, model_id="reference_v1",
        artifact_id="artifact-reference-1", calibrator_id="calibrator-reference-1",
        model_timestamp=NOW, calibrated_probability=.64,
        calibrated_lower_bound=.53, calibrated_upper_bound=.73,
    )
    fields.update(kw)
    return ModelResult(**fields)

class ReferenceSelectorTest(unittest.TestCase):
    def select(self, c=None, p=None, reg=None, r=None, **kw):
        c=c or candidate()
        return choose_reference_route(c, p or failed(c), reg if reg is not None else registration(),
                                      r if r is not None else result(c), **kw)

    def test_certified_reference_selected_without_publication_authority(self):
        s=self.select()
        self.assertEqual((s.controlling_source,s.controlling_model_id),
                         ("REFERENCE", "reference_v1"))
        self.assertEqual(s.primary_status, "MODEL_SCORER_FAILED")
        self.assertTrue(s.reducer_required)
        self.assertFalse(s.selector_is_publication_authority)
        self.assertFalse(s.can_execute)
        self.assertFalse(CAN_EXECUTE)
        self.assertEqual(TERMINAL_AUTHORITY, "V17_TERMINAL_REDUCER")

    def test_valid_primary_retains_control_even_if_a_fallback_is_present(self):
        c=candidate()
        p=replace(result(c),model_id="primary_v17",artifact_id="primary-artifact",
                  calibrator_id="primary-calibrator")
        self.assertEqual(self.select(c,p=p).controlling_source, "PRIMARY")

    def test_primary_valid_but_malformed_not_reinterpreted_as_failure(self):
        c=candidate()
        p=replace(result(c),model_id="primary_v17",calibrated_lower_bound=.95)
        s=self.select(c,p=p)
        self.assertEqual(s.reason, "PRIMARY_VALID_STATUS_WITH_INVALID_PACKAGE")
        self.assertEqual(s.controlling_source, "NONE")

    def test_primary_downstream_rejected_status_never_fallbacks(self):
        self.assertEqual(self.select(p=failed(status="MODEL_QUALIFIED_HOLD")).reason,
                         "PRIMARY_STATUS_NOT_FAILOVER_ELIGIBLE")

    def test_no_fallback_for_unsupported_primary_status(self):
        self.assertEqual(self.select(p=failed(status="ACTION_TRANSPORT_TIMEOUT")).controlling_source,"NONE")

    def test_fallback_disabled_or_not_certified(self):
        for reg in (registration(active=False),registration(certified=False)):
            self.assertEqual(self.select(reg=reg).reason,"REFERENCE_NOT_CERTIFIED_ACTIVE")

    def test_rejects_missing_independent_review_and_certification(self):
        for reg in (registration(independent_review_id=""),registration(certification_id="")):
            self.assertEqual(self.select(reg=reg).reason,"REFERENCE_PROVENANCE_MISSING")

    def test_rejects_exact_line_mismatch(self):
        self.assertEqual(self.select(reg=registration(exact_lines=(39.5,))).reason,
                         "REFERENCE_EXACT_LINE_UNSUPPORTED")

    def test_rejects_wrong_sport_market_and_settlement(self):
        for reg in (registration(sport="NBA"),registration(stat_family="RECEIVING_YARDS"),
                    registration(settlement_id="first_half_yards")):
            self.assertEqual(self.select(reg=reg).reason, "REFERENCE_SCOPE_MISMATCH")

    def test_rejects_uncertified_failure_type(self):
        self.assertEqual(
            self.select(p=failed(status="MODEL_OUTPUT_INVALID"),
                        reg=registration(allowed_primary_failures=("MODEL_SCORER_FAILED",))).reason,
            "PRIMARY_FAILURE_NOT_IN_CERTIFIED_SCOPE")

    def test_rejects_identity_and_upstream_blockers(self):
        self.assertEqual(self.select(upstream_blockers=("IDENTITY_CONFLICT",)).reason,
                         "UPSTREAM_BLOCKER_PRESERVED")
        self.assertEqual(self.select(c=candidate(official_event_id="")).reason,
                         "EXACT_IDENTITY_UNRESOLVED")

    def test_no_live_or_postponed_pregame_fallback(self):
        for event_status in ("LIVE","FINAL","POSTPONED","CANCELED"):
            self.assertEqual(self.select(c=candidate(event_status=event_status)).reason,
                             "NON_PREGAME_EVENT")

    def test_invalid_prop_identity(self):
        for c in (candidate(direction=None),candidate(exact_line=None),candidate(stat_family=None)):
            self.assertEqual(self.select(c=c).reason, "EXACT_PROP_IDENTITY_UNRESOLVED")

    def test_rejects_stale_reference_result(self):
        c=candidate()
        self.assertEqual(self.select(c=c,r=result(c,model_timestamp=NOW-timedelta(minutes=6))).reason,
                         "REFERENCE_RESULT_INVALID_OR_STALE")

    def test_rejects_probability_package_outside_bounds(self):
        for r in (result(calibrated_probability=1.2),result(calibrated_lower_bound=.72,calibrated_probability=.6),
                  result(calibrated_upper_bound=float("nan")),result(calibrated_probability=True)):
            self.assertEqual(self.select(r=r).reason,"REFERENCE_RESULT_INVALID_OR_STALE")

    def test_rejects_wrong_artifact_or_calibrator(self):
        self.assertEqual(self.select(r=result(calibrator_id="other")).reason,
                         "REFERENCE_ARTIFACT_OR_CALIBRATOR_MISMATCH")

    def test_never_reuses_primary_model_identity(self):
        self.assertEqual(self.select(reg=registration(model_id="primary_v17")).reason,
                         "REFERENCE_NOT_INDEPENDENT_OF_PRIMARY")

    def test_certification_time_window_strict(self):
        self.assertEqual(self.select(reg=registration(valid_until=NOW-timedelta(seconds=1))).reason,
                         "REFERENCE_CERTIFICATION_EXPIRED_OR_NOT_YET_VALID")

    def test_missing_inputs_require_exact_certified_regime(self):
        c=candidate(missing_model_inputs=("injury_status",))
        p=failed(c,status="MODEL_INPUTS_INSUFFICIENT")
        self.assertEqual(self.select(c=c,p=p).reason,"MISSING_INPUT_REGIME_UNCERTIFIED")
        self.assertEqual(self.select(c=c,p=p,reg=registration(certified_missing_inputs=("injury_status",)),
                         r=result(c)).controlling_source,"REFERENCE")
        self.assertEqual(self.select(c=c,p=p,reg=registration(certified_missing_inputs=("lineup",)),
                         r=result(c)).reason,"MISSING_INPUT_REGIME_UNCERTIFIED")

    def test_other_failures_cannot_hide_missing_inputs(self):
        c=candidate(missing_model_inputs=("lineup",))
        self.assertEqual(self.select(c=c).reason,"UNRESOLVED_MISSING_INPUTS")

    def test_primary_identity_mismatch_blocks(self):
        self.assertEqual(self.select(p=failed(candidate(candidate_id="wrong"))).reason,
                         "PRIMARY_IDENTITY_MISMATCH")

    def test_primary_status_and_model_id_preserved_if_fallback_invalid(self):
        s=self.select(r=result(model_timestamp=NOW-timedelta(days=2)))
        self.assertEqual((s.primary_status,s.controlling_source),
                         ("MODEL_SCORER_FAILED","NONE"))

if __name__ == "__main__":
    unittest.main()
