"""
gate_engine/tests/test_model_validation.py
WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4-MODELVAL

Regression tests for the shared Model Consistency & Validation layer.

Coverage
--------
TestFeatureStore           — commit/immutability/filtering
TestModelManifest          — provenance recording, duplicate rejection
TestChampionChallenger     — governance-pinned promotion, no auto-promotion
TestCalibrationScoreboard  — Brier accumulation, family aggregation
TestDriftMonitor           — JS divergence, status thresholds
TestHealthStateMachine     — state transitions, invalid transitions rejected
TestLearningSchedule       — fast refresh / slow update blocking without pin
TestPromotionGate          — checklist evaluation, never auto-approves
TestWalkForward            — replay in order, fail-closed on scoring errors
TestValidationWrapper      — full B4 wrap, followup blockers present
TestModelValidationInvariants — can_execute=False throughout
"""
from __future__ import annotations

import unittest

from gate_engine.universal_agent.model_validation.feature_store import (
    PointInTimeFeatureStore, FeatureSnapshot,
    can_execute as FS_CAN_EXECUTE,
)
from gate_engine.universal_agent.model_validation.model_manifest import (
    ModelManifest, ManifestEntry, _hash_params,
    can_execute as MM_CAN_EXECUTE,
)
from gate_engine.universal_agent.model_validation.champion_challenger import (
    ChampionChallengerRegistry,
    can_execute as CC_CAN_EXECUTE,
    NO_AUTO_PROMOTION,
)
from gate_engine.universal_agent.model_validation.calibration_scoreboard import (
    CalibrationScoreboard,
    can_execute as CS_CAN_EXECUTE,
)
from gate_engine.universal_agent.model_validation.drift_monitor import (
    DriftMonitor, DriftStatus, _js_divergence_from_samples,
    can_execute as DM_CAN_EXECUTE,
)
from gate_engine.universal_agent.model_validation.health_state import (
    ModelHealthStateMachine, HealthState,
    can_execute as HS_CAN_EXECUTE,
)
from gate_engine.universal_agent.model_validation.learning_schedule import (
    TwoSpeedLearningSchedule, ChannelType,
    can_execute as LS_CAN_EXECUTE,
)
from gate_engine.universal_agent.model_validation.promotion_gate import (
    PromotionGate, PromotionStatus,
    can_execute as PG_CAN_EXECUTE,
    NO_AUTO_PROMOTION as PG_NO_AUTO_PROMOTION,
)
from gate_engine.universal_agent.model_validation.walk_forward import (
    WalkForwardReplayEngine,
    can_execute as WF_CAN_EXECUTE,
)
from gate_engine.universal_agent.model_validation.validation_wrapper import (
    ModelValidationWrapper, ValidatedAdapterResult,
    can_execute as VW_CAN_EXECUTE,
)
from gate_engine.universal_agent.model_validation import (
    can_execute as MV_CAN_EXECUTE,
    NO_AUTO_PROMOTION as MV_NO_AUTO_PROMOTION,
    NO_FORMULA_MUTATION,
    PRODUCTION_AUTHORITY,
    USER_OUTPUT_AUTHORITY,
    CAPITAL_AUTHORITY,
)


# ── TestFeatureStore ──────────────────────────────────────────────────────────

class TestFeatureStore(unittest.TestCase):

    def test_commit_and_retrieve(self):
        store = PointInTimeFeatureStore()
        snap = store.commit_snapshot(
            snapshot_id="snap-001", as_of_date="2026-08-01",
            features={"reb_avg": 10.5, "min_avg": 32.0},
        )
        self.assertIsInstance(snap, FeatureSnapshot)
        self.assertEqual(store.get_snapshot("snap-001"), snap)

    def test_snapshot_is_frozen(self):
        store = PointInTimeFeatureStore()
        snap = store.commit_snapshot(
            snapshot_id="snap-002", as_of_date="2026-08-01",
            features={"x": 1},
        )
        with self.assertRaises((AttributeError, TypeError)):
            snap.as_of_date = "mutated"  # type: ignore[misc]

    def test_duplicate_snapshot_id_raises(self):
        store = PointInTimeFeatureStore()
        store.commit_snapshot(snapshot_id="snap-dup", as_of_date="2026-08-01",
                              features={"x": 1})
        with self.assertRaises(ValueError):
            store.commit_snapshot(snapshot_id="snap-dup", as_of_date="2026-08-02",
                                  features={"x": 2})

    def test_get_nonexistent_returns_none(self):
        store = PointInTimeFeatureStore()
        self.assertIsNone(store.get_snapshot("missing"))

    def test_list_snapshots_before_filters_correctly(self):
        store = PointInTimeFeatureStore()
        store.commit_snapshot(snapshot_id="s1", as_of_date="2026-07-01", features={})
        store.commit_snapshot(snapshot_id="s2", as_of_date="2026-08-01", features={})
        store.commit_snapshot(snapshot_id="s3", as_of_date="2026-09-01", features={})
        before = store.list_snapshots_before("2026-08-01")
        ids = {s.snapshot_id for s in before}
        self.assertIn("s1", ids)
        self.assertIn("s2", ids)
        self.assertNotIn("s3", ids)

    def test_list_snapshots_sorted_chronologically(self):
        store = PointInTimeFeatureStore()
        store.commit_snapshot(snapshot_id="sb", as_of_date="2026-08-10", features={})
        store.commit_snapshot(snapshot_id="sa", as_of_date="2026-07-01", features={})
        snaps = store.list_snapshots_before("2026-12-31")
        dates = [s.as_of_date for s in snaps]
        self.assertEqual(dates, sorted(dates))

    def test_count_and_ids(self):
        store = PointInTimeFeatureStore()
        store.commit_snapshot(snapshot_id="x1", as_of_date="2026-08-01", features={})
        store.commit_snapshot(snapshot_id="x2", as_of_date="2026-08-02", features={})
        self.assertEqual(store.count(), 2)
        self.assertIn("x1", store.snapshot_ids())

    def test_can_execute_false(self):
        self.assertFalse(FS_CAN_EXECUTE)


# ── TestModelManifest ─────────────────────────────────────────────────────────

class TestModelManifest(unittest.TestCase):

    def _manifest(self):
        m = ModelManifest()
        return m, m.record(
            run_id="run-001", model_id="wnba_reb_v1", model_version="1.0.0",
            params={"alpha": 0.5}, feature_snapshot_ids=["snap-1"],
            stat_key="rebounds", sport="WNBA",
        )

    def test_record_returns_entry(self):
        _, entry = self._manifest()
        self.assertIsInstance(entry, ManifestEntry)
        self.assertEqual(entry.run_id, "run-001")

    def test_entry_is_frozen(self):
        _, entry = self._manifest()
        with self.assertRaises((AttributeError, TypeError)):
            entry.model_id = "mutated"  # type: ignore[misc]

    def test_duplicate_run_id_raises(self):
        m, _ = self._manifest()
        with self.assertRaises(ValueError):
            m.record(
                run_id="run-001", model_id="other", model_version="1.0.0",
                params={}, feature_snapshot_ids=[], stat_key="x", sport="WNBA",
            )

    def test_param_hash_deterministic(self):
        h1 = _hash_params({"alpha": 0.5, "beta": 0.1})
        h2 = _hash_params({"beta": 0.1, "alpha": 0.5})
        self.assertEqual(h1, h2)

    def test_list_by_model(self):
        m = ModelManifest()
        m.record(run_id="r1", model_id="model_A", model_version="1.0",
                 params={}, feature_snapshot_ids=[], stat_key="reb", sport="WNBA")
        m.record(run_id="r2", model_id="model_B", model_version="1.0",
                 params={}, feature_snapshot_ids=[], stat_key="pts", sport="WNBA")
        entries = m.list_by_model("model_A")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].model_id, "model_A")

    def test_list_by_sport_stat(self):
        m = ModelManifest()
        m.record(run_id="r1", model_id="M1", model_version="1.0",
                 params={}, feature_snapshot_ids=[], stat_key="rebounds", sport="WNBA")
        m.record(run_id="r2", model_id="M2", model_version="1.0",
                 params={}, feature_snapshot_ids=[], stat_key="points", sport="WNBA")
        reb_entries = m.list_by_sport_stat("WNBA", "rebounds")
        self.assertEqual(len(reb_entries), 1)

    def test_can_execute_false(self):
        self.assertFalse(MM_CAN_EXECUTE)


# ── TestChampionChallenger ────────────────────────────────────────────────────

class TestChampionChallenger(unittest.TestCase):

    def test_set_champion_requires_governance_pin(self):
        reg = ChampionChallengerRegistry()
        with self.assertRaises(ValueError):
            reg.set_champion("WNBA", "rebounds", "model_A", governance_pin="")

    def test_set_champion_with_pin(self):
        reg = ChampionChallengerRegistry()
        reg.set_champion("WNBA", "rebounds", "model_A", governance_pin="gov-pin-001")
        self.assertEqual(reg.get_champion("WNBA", "rebounds"), "model_A")

    def test_no_auto_promotion_constant(self):
        self.assertTrue(NO_AUTO_PROMOTION)

    def test_add_challenger(self):
        reg = ChampionChallengerRegistry()
        reg.set_champion("WNBA", "rebounds", "champ", governance_pin="pin-1")
        reg.add_challenger("WNBA", "rebounds", "challenger_A")
        self.assertIn("challenger_A", reg.get_challengers("WNBA", "rebounds"))

    def test_add_champion_as_challenger_raises(self):
        reg = ChampionChallengerRegistry()
        reg.set_champion("WNBA", "rebounds", "champ", governance_pin="pin-1")
        with self.assertRaises(ValueError):
            reg.add_challenger("WNBA", "rebounds", "champ")

    def test_max_challengers_enforced(self):
        reg = ChampionChallengerRegistry()
        reg.set_champion("WNBA", "rebounds", "champ", governance_pin="pin-1")
        reg.add_challenger("WNBA", "rebounds", "c1")
        reg.add_challenger("WNBA", "rebounds", "c2")
        reg.add_challenger("WNBA", "rebounds", "c3")
        with self.assertRaises(ValueError):
            reg.add_challenger("WNBA", "rebounds", "c4")

    def test_remove_challenger(self):
        reg = ChampionChallengerRegistry()
        reg.set_champion("WNBA", "rebounds", "champ", governance_pin="pin-1")
        reg.add_challenger("WNBA", "rebounds", "c1")
        reg.remove_challenger("WNBA", "rebounds", "c1")
        self.assertNotIn("c1", reg.get_challengers("WNBA", "rebounds"))

    def test_status_includes_no_auto_promotion(self):
        reg = ChampionChallengerRegistry()
        reg.set_champion("WNBA", "rebounds", "champ", governance_pin="pin-1")
        status = reg.status("WNBA", "rebounds")
        self.assertTrue(status["no_auto_promotion"])

    def test_promotion_is_audited(self):
        reg = ChampionChallengerRegistry()
        reg.set_champion("WNBA", "rebounds", "m1", governance_pin="pin-1")
        reg.set_champion("WNBA", "rebounds", "m2", governance_pin="pin-2")
        status = reg.status("WNBA", "rebounds")
        self.assertEqual(status["promotion_count"], 2)

    def test_can_execute_false(self):
        self.assertFalse(CC_CAN_EXECUTE)


# ── TestCalibrationScoreboard ─────────────────────────────────────────────────

class TestCalibrationScoreboard(unittest.TestCase):

    def test_record_and_retrieve_brier(self):
        cs = CalibrationScoreboard()
        cs.record_observation(
            sport="WNBA", stat_key="rebounds", model_id="M1",
            model_family="poisson_v1",
            predicted_prob=0.6, actual_outcome=1.0,
        )
        brier = cs.get_brier("WNBA", "rebounds", "M1")
        self.assertIsNotNone(brier)
        self.assertAlmostEqual(brier, (0.6 - 1.0) ** 2, places=6)

    def test_accumulates_correctly(self):
        cs = CalibrationScoreboard()
        cs.record_observation(sport="WNBA", stat_key="rebounds", model_id="M1",
                              model_family="fam", predicted_prob=0.7, actual_outcome=1.0)
        cs.record_observation(sport="WNBA", stat_key="rebounds", model_id="M1",
                              model_family="fam", predicted_prob=0.3, actual_outcome=0.0)
        brier = cs.get_brier("WNBA", "rebounds", "M1")
        expected = ((0.7 - 1.0) ** 2 + (0.3 - 0.0) ** 2) / 2
        self.assertAlmostEqual(brier, expected, places=6)

    def test_invalid_prob_raises(self):
        cs = CalibrationScoreboard()
        with self.assertRaises(ValueError):
            cs.record_observation(sport="WNBA", stat_key="reb", model_id="M",
                                  model_family="f", predicted_prob=1.5, actual_outcome=1.0)

    def test_invalid_outcome_raises(self):
        cs = CalibrationScoreboard()
        with self.assertRaises(ValueError):
            cs.record_observation(sport="WNBA", stat_key="reb", model_id="M",
                                  model_family="f", predicted_prob=0.5, actual_outcome=0.5)

    def test_family_summary(self):
        cs = CalibrationScoreboard()
        for _ in range(5):
            cs.record_observation(sport="WNBA", stat_key="reb", model_id="M1",
                                  model_family="fam_A", predicted_prob=0.6, actual_outcome=1.0)
        summary = cs.family_summary("fam_A")
        self.assertEqual(summary["total_settled"], 5)
        self.assertIsNotNone(summary["family_brier"])

    def test_nonexistent_returns_none(self):
        cs = CalibrationScoreboard()
        self.assertIsNone(cs.get_brier("WNBA", "rebounds", "nonexistent"))

    def test_can_execute_false(self):
        self.assertFalse(CS_CAN_EXECUTE)


# ── TestDriftMonitor ──────────────────────────────────────────────────────────

class TestDriftMonitor(unittest.TestCase):

    def test_no_drift_identical_distributions(self):
        dm = DriftMonitor("M1")
        vals = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        dm.add_reference_values("reb_avg", vals)
        dm.add_current_values("reb_avg", vals)
        summary = dm.compute_drift()
        rep = summary.feature_reports[0]
        self.assertEqual(rep.status, DriftStatus.NOMINAL)

    def test_alert_on_completely_different_distributions(self):
        dm = DriftMonitor("M1")
        # Use spread distributions so histograms occupy different bins (not point masses)
        dm.add_reference_values("reb_avg", [float(i) for i in range(20)])       # 0–19
        dm.add_current_values("reb_avg", [float(i + 200) for i in range(20)])   # 200–219
        summary = dm.compute_drift()
        rep = next(r for r in summary.feature_reports if r.feature_name == "reb_avg")
        self.assertEqual(rep.status, DriftStatus.ALERT)

    def test_unknown_status_insufficient_data(self):
        dm = DriftMonitor("M1")
        dm.add_reference_values("x", [1.0])  # only 1 sample
        dm.add_current_values("x", [1.0])
        summary = dm.compute_drift()
        rep = summary.feature_reports[0]
        self.assertEqual(rep.status, DriftStatus.UNKNOWN)

    def test_js_divergence_bounded(self):
        js = _js_divergence_from_samples([1.0] * 10, [100.0] * 10)
        self.assertGreaterEqual(js, 0.0)
        self.assertLessEqual(js, 1.0)

    def test_js_divergence_zero_for_identical(self):
        js = _js_divergence_from_samples([5.0] * 10, [5.0] * 10)
        self.assertAlmostEqual(js, 0.0, places=4)

    def test_alert_features_populated(self):
        dm = DriftMonitor("M1")
        # Spread distributions, not point masses, so JS divergence is non-zero
        dm.add_reference_values("bad_feat", [float(i) for i in range(30)])       # 0–29
        dm.add_current_values("bad_feat", [float(i + 500) for i in range(30)])   # 500–529
        summary = dm.compute_drift()
        self.assertIn("bad_feat", summary.alert_features)

    def test_can_execute_false(self):
        self.assertFalse(DM_CAN_EXECUTE)


# ── TestHealthStateMachine ────────────────────────────────────────────────────

class TestHealthStateMachine(unittest.TestCase):

    def test_initial_state_healthy(self):
        sm = ModelHealthStateMachine("M1")
        self.assertEqual(sm.state, HealthState.HEALTHY)

    def test_healthy_to_degraded(self):
        sm = ModelHealthStateMachine("M1")
        sm.transition(HealthState.DEGRADED, reason="brier above threshold")
        self.assertEqual(sm.state, HealthState.DEGRADED)

    def test_degraded_to_suspended(self):
        sm = ModelHealthStateMachine("M1")
        sm.transition(HealthState.DEGRADED, reason="drift alert")
        sm.transition(HealthState.SUSPENDED, reason="manual halt")
        self.assertEqual(sm.state, HealthState.SUSPENDED)

    def test_any_to_quarantined(self):
        sm = ModelHealthStateMachine("M1")
        sm.transition(HealthState.QUARANTINED, reason="investigation")
        self.assertEqual(sm.state, HealthState.QUARANTINED)

    def test_invalid_transition_raises(self):
        sm = ModelHealthStateMachine("M1")
        with self.assertRaises(ValueError):
            sm.transition(HealthState.SUSPENDED, reason="skip degraded")

    def test_missing_reason_raises(self):
        sm = ModelHealthStateMachine("M1")
        with self.assertRaises(ValueError):
            sm.transition(HealthState.DEGRADED, reason="")

    def test_operational_only_when_healthy_or_degraded(self):
        sm = ModelHealthStateMachine("M1")
        self.assertTrue(sm.is_operational())
        sm.transition(HealthState.DEGRADED, reason="x")
        self.assertTrue(sm.is_operational())
        sm.transition(HealthState.SUSPENDED, reason="y")
        self.assertFalse(sm.is_operational())

    def test_history_recorded(self):
        sm = ModelHealthStateMachine("M1")
        sm.transition(HealthState.DEGRADED, reason="drift")
        self.assertEqual(len(sm.history()), 1)
        self.assertEqual(sm.history()[0]["to_state"], HealthState.DEGRADED)

    def test_can_execute_false(self):
        self.assertFalse(HS_CAN_EXECUTE)


# ── TestLearningSchedule ──────────────────────────────────────────────────────

class TestLearningSchedule(unittest.TestCase):

    def test_fast_refresh_recorded(self):
        sched = TwoSpeedLearningSchedule("M1")
        ev = sched.record_fast_refresh()
        self.assertEqual(ev.event_type, "FEATURE_REFRESH")
        self.assertEqual(ev.channel, ChannelType.FAST)

    def test_slow_update_blocked_without_pin(self):
        sched = TwoSpeedLearningSchedule("M1")
        ev = sched.record_weight_update_request()
        self.assertEqual(ev.event_type, "WEIGHT_UPDATE_BLOCKED")

    def test_slow_update_requested_with_pin(self):
        sched = TwoSpeedLearningSchedule("M1")
        ev = sched.record_weight_update_request(governance_pin="gov-pin-xyz")
        self.assertEqual(ev.event_type, "WEIGHT_UPDATE_REQUESTED")

    def test_summary_counts_correctly(self):
        sched = TwoSpeedLearningSchedule("M1")
        sched.record_fast_refresh()
        sched.record_fast_refresh()
        sched.record_weight_update_request()  # blocked
        sched.record_weight_update_request(governance_pin="pin-1")  # requested
        s = sched.summary()
        self.assertEqual(s["fast_refreshes"], 2)
        self.assertEqual(s["slow_blocked"], 1)
        self.assertEqual(s["slow_requested"], 1)
        self.assertTrue(s["no_auto_weight_update"])

    def test_can_execute_false(self):
        self.assertFalse(LS_CAN_EXECUTE)


# ── TestPromotionGate ─────────────────────────────────────────────────────────

class TestPromotionGate(unittest.TestCase):

    def _all_pass_kwargs(self, governance_approved=False):
        return dict(
            model_id="M1",
            calibration_threshold_met=True,
            drift_acceptable=True,
            health_state_ok=True,
            n_settled_sufficient=True,
            manual_sign_off=True,
            governance_approved=governance_approved,
        )

    def test_blocked_when_checklist_fails(self):
        gate = PromotionGate()
        dec = gate.evaluate(**{**self._all_pass_kwargs(),
                               "calibration_threshold_met": False})
        self.assertEqual(dec.status, PromotionStatus.BLOCKED)
        self.assertIn("calibration_threshold_met", dec.blocking_items)

    def test_pending_when_checklist_pass_but_no_governance(self):
        gate = PromotionGate()
        dec = gate.evaluate(**self._all_pass_kwargs(governance_approved=False))
        self.assertEqual(dec.status, PromotionStatus.PENDING)

    def test_approved_when_all_pass_and_governance(self):
        gate = PromotionGate()
        dec = gate.evaluate(**self._all_pass_kwargs(governance_approved=True))
        self.assertEqual(dec.status, PromotionStatus.APPROVED)

    def test_never_auto_approves(self):
        """governance_approved must be explicitly True — never inferred."""
        gate = PromotionGate()
        dec = gate.evaluate(**self._all_pass_kwargs(governance_approved=False))
        self.assertNotEqual(dec.status, PromotionStatus.APPROVED)

    def test_rollback_requires_governance(self):
        gate = PromotionGate()
        result = gate.evaluate_rollback(
            model_id="M1",
            challenger_has_manifest=True,
            champion_exists=True,
            governance_approved=False,
        )
        self.assertFalse(result["rollback_allowed"])

    def test_rollback_allowed_with_governance(self):
        gate = PromotionGate()
        result = gate.evaluate_rollback(
            model_id="M1",
            challenger_has_manifest=True,
            champion_exists=True,
            governance_approved=True,
        )
        self.assertTrue(result["rollback_allowed"])
        self.assertTrue(result["no_auto_rollback"])

    def test_no_auto_promotion_constant(self):
        self.assertTrue(PG_NO_AUTO_PROMOTION)

    def test_can_execute_false(self):
        self.assertFalse(PG_CAN_EXECUTE)


# ── TestWalkForward ───────────────────────────────────────────────────────────

class TestWalkForward(unittest.TestCase):

    def _store_with_snaps(self):
        from gate_engine.universal_agent.model_validation.feature_store import (
            PointInTimeFeatureStore,
        )
        store = PointInTimeFeatureStore()
        snaps = []
        for i, date in enumerate(["2026-07-01", "2026-07-15", "2026-08-01"]):
            s = store.commit_snapshot(
                snapshot_id=f"s{i}", as_of_date=date,
                features={"reb_avg": 10.0 + i},
            )
            snaps.append(s)
        return snaps

    def test_replay_in_order(self):
        engine = WalkForwardReplayEngine()
        snaps = self._store_with_snaps()
        result = engine.replay(
            model_id="M1",
            snapshots=snaps,
            scoring_fn=lambda f: f.get("reb_avg"),
        )
        self.assertEqual(result.n_steps, 3)
        self.assertEqual(result.predictions, [10.0, 11.0, 12.0])

    def test_failed_steps_counted(self):
        engine = WalkForwardReplayEngine()
        snaps = self._store_with_snaps()

        def bad_fn(f):
            raise ValueError("intentional")

        result = engine.replay(model_id="M1", snapshots=snaps, scoring_fn=bad_fn)
        self.assertEqual(result.n_failed, 3)
        self.assertEqual(result.n_successful, 0)

    def test_date_filtering_min(self):
        engine = WalkForwardReplayEngine()
        snaps = self._store_with_snaps()
        result = engine.replay(
            model_id="M1", snapshots=snaps,
            scoring_fn=lambda f: f.get("reb_avg"),
            min_date="2026-07-15",
        )
        self.assertEqual(result.n_steps, 2)

    def test_mean_prediction_computed(self):
        engine = WalkForwardReplayEngine()
        snaps = self._store_with_snaps()
        result = engine.replay(
            model_id="M1", snapshots=snaps,
            scoring_fn=lambda f: f.get("reb_avg"),
        )
        self.assertAlmostEqual(result.mean_prediction, 11.0, places=4)

    def test_can_execute_false(self):
        self.assertFalse(WF_CAN_EXECUTE)


# ── TestValidationWrapper ─────────────────────────────────────────────────────

class TestValidationWrapper(unittest.TestCase):

    def _wrap(self, **overrides):
        wrapper = ModelValidationWrapper()
        kwargs = dict(
            adapter_result={"mock": "adapter_result"},
            run_id="wrap-run-001",
            model_id="wnba_reb_v1",
            stat_key="rebounds",
            sport="WNBA",
        )
        kwargs.update(overrides)
        return wrapper.wrap(**kwargs)

    def test_returns_validated_result(self):
        r = self._wrap()
        self.assertIsInstance(r, ValidatedAdapterResult)

    def test_run_manifest_present(self):
        r = self._wrap()
        self.assertIn("run_id", r.run_manifest)
        self.assertIn("param_hash", r.run_manifest)

    def test_promotion_pending_without_governance(self):
        r = self._wrap(governance_approved=False)
        # Without calibration data, gate should be BLOCKED or PENDING
        self.assertIn(r.promotion_status, [PromotionStatus.PENDING, PromotionStatus.BLOCKED])

    def test_promotion_never_auto_approved(self):
        # Even with perfect checklist, governance_approved=False → not APPROVED
        r = self._wrap(
            brier_score=0.10, n_settled=100,
            drift_status="NOMINAL", health_state="HEALTHY",
            governance_approved=False, manual_sign_off=True,
        )
        self.assertNotEqual(r.promotion_status, PromotionStatus.APPROVED)

    def test_followup_blockers_present(self):
        r = self._wrap()
        blockers = r.validation_summary.get("followup_blockers", [])
        self.assertTrue(any("FOLLOWUP_193" in b for b in blockers))
        self.assertTrue(any("FOLLOWUP_195" in b for b in blockers))

    def test_ceiling_model_qualified_hold(self):
        r = self._wrap()
        self.assertEqual(r.validation_summary.get("ceiling"), "MODEL_QUALIFIED_HOLD")

    def test_can_execute_false_in_summary(self):
        r = self._wrap()
        self.assertFalse(r.validation_summary.get("can_execute", True))

    def test_no_auto_promotion_in_summary(self):
        r = self._wrap()
        self.assertTrue(r.validation_summary.get("no_auto_promotion", False))

    def test_can_execute_false(self):
        self.assertFalse(VW_CAN_EXECUTE)


# ── TestModelValidationInvariants ─────────────────────────────────────────────

class TestModelValidationInvariants(unittest.TestCase):
    """Confirm all governance invariants at the module level."""

    def test_package_can_execute_false(self):
        self.assertFalse(MV_CAN_EXECUTE)

    def test_no_auto_promotion_true(self):
        self.assertTrue(MV_NO_AUTO_PROMOTION)

    def test_no_formula_mutation_true(self):
        self.assertTrue(NO_FORMULA_MUTATION)

    def test_production_authority_false(self):
        self.assertFalse(PRODUCTION_AUTHORITY)

    def test_user_output_authority_false(self):
        self.assertFalse(USER_OUTPUT_AUTHORITY)

    def test_capital_authority_false(self):
        self.assertFalse(CAPITAL_AUTHORITY)

    def test_all_submodule_can_execute_false(self):
        for flag, name in [
            (FS_CAN_EXECUTE,  "feature_store"),
            (MM_CAN_EXECUTE,  "model_manifest"),
            (CC_CAN_EXECUTE,  "champion_challenger"),
            (CS_CAN_EXECUTE,  "calibration_scoreboard"),
            (DM_CAN_EXECUTE,  "drift_monitor"),
            (HS_CAN_EXECUTE,  "health_state"),
            (LS_CAN_EXECUTE,  "learning_schedule"),
            (PG_CAN_EXECUTE,  "promotion_gate"),
            (WF_CAN_EXECUTE,  "walk_forward"),
            (VW_CAN_EXECUTE,  "validation_wrapper"),
        ]:
            with self.subTest(module=name):
                self.assertFalse(flag, f"{name}.can_execute must be False")


if __name__ == "__main__":
    unittest.main()
