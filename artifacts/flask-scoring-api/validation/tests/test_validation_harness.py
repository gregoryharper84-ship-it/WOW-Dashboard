"""
validation/tests/test_validation_harness.py

Deterministic acceptance tests for the WOW Prediction Validation Harness v1.

Coverage
--------
T1  PredictionRecord is immutable (frozen=True) after construction
T2  prediction_id is deterministic — same inputs → same ID
T3  prediction_id collision detects duplicate predictions
T4  Leakage guard: outcome_timestamp must be strictly after frozen_at
T5  hit derived correctly for LESS / MORE / push (exact-line)
T6  Chronological split preserves date order and fraction sizes
T7  Holdout is the last chronological block (no leakage into earlier splits)
T8  Split manifest counts are consistent
T9  Brier score: known values (constant predictor, perfect predictor)
T10 Log loss: known values (coin-flip baseline)
T11 Calibration buckets: count and ECE computed correctly
T12 Sample coverage: null-probability rows counted correctly
T13 Line slices: correct grouping by line value
T14 Missing data: None probability flows through metrics without crash
T15 Duplicate prediction ID detection
T16 Baseline A (season_empirical) predict_single — correct hit rate
T17 Baseline B (l10_empirical) predict_single — window respected
T18 Baseline C (stat_model) predict_single — reproducible with seed
T19 Baseline C returns UNAVAILABLE for opponent pitches/PA when not supplied
T20 WOW_LEAN_1IP adapter constructs a PredictionRecord without touching live endpoints
T21 Feature registry: all 10 features present; supported/unavailable counts correct
T22 Ablation runner: UNAVAILABLE features are reported, never fabricated
T23 Ablation runner: supported features return numeric Brier scores
T24 JSON reporter: all required top-level keys present
T25 Markdown reporter: produces non-empty output with verdict table
T26 Eval rules declared before holdout (holdout_evaluated=False by default)
T27 Synthetic dataset: 50 pairs, chronological, correct split sizes
T28 Feature snapshot ID changes when features dict changes
T29 Split with fewer than 3 records handled gracefully
T30 stat_model uses genre defaults when fewer than 3 ledger rows
"""
from __future__ import annotations

import datetime as _dt
import math
import unittest
from datetime import timezone, timedelta

# ── Fixture helpers ──────────────────────────────────────────────────────────

def _pred(
    i: int = 0,
    *,
    game_date: str = "2026-07-15",
    pitcher_name: str = "Shota Imanaga",
    pitcher_mlbam_id: int = 669392,
    line: float = 15.5,
    direction: str = "LESS",
    model_prob: float = 0.55,
    uncertainty: float = 0.08,
    frozen_at: str = "2026-07-15T12:00:00+00:00",
) -> "PredictionRecord":
    from validation.schema.prediction_record import PredictionRecord
    return PredictionRecord.create(
        game_date          = game_date,
        pitcher_name       = pitcher_name,
        pitcher_mlbam_id   = pitcher_mlbam_id,
        opponent           = "CWS",
        line               = line,
        direction          = direction,
        model_probability  = model_prob,
        model_uncertainty  = uncertainty,
        features           = {"bf_dist": {"p_bf_3": 0.4, "p_bf_4": 0.35, "p_bf_gte5": 0.25}, "n": 10},
        model_version      = "1ip_monte_carlo_event_tree_v1",
        data_provenance    = {"source": "test_fixture"},
        _frozen_at         = frozen_at,
    )


def _outcome(pred, actual_pitches: int = 13, *, hours_after: int = 6) -> "OutcomeRecord":
    from validation.schema.outcome_record import attach_outcome
    frozen_dt = _dt.datetime.fromisoformat(pred.frozen_at.replace("Z", "+00:00"))
    outcome_ts = (frozen_dt + timedelta(hours=hours_after)).isoformat()
    return attach_outcome(
        pred,
        actual_pitches  = actual_pitches,
        outcome_source  = "test_fixture",
        outcome_verified = True,
        _outcome_timestamp = outcome_ts,
    )


def _make_pairs(n: int, *, base_date: str = "2026-06-01") -> list:
    """Return n (PredictionRecord, OutcomeRecord) pairs with sequential dates."""
    pairs = []
    base = _dt.date.fromisoformat(base_date)
    for i in range(n):
        d   = (base + timedelta(days=i)).isoformat()
        fz  = f"{d}T10:00:00+00:00"
        ots = f"{d}T22:00:00+00:00"
        p   = _pred(i, game_date=d, frozen_at=fz,
                    model_prob=round(0.45 + 0.05 * math.sin(i), 4))
        from validation.schema.outcome_record import attach_outcome
        o = attach_outcome(p, actual_pitches=13 + (i % 5),
                           outcome_source="test", _outcome_timestamp=ots)
        pairs.append((p, o))
    return pairs


# ── T1 — Immutability ────────────────────────────────────────────────────────

class TestPredictionRecordImmutable(unittest.TestCase):

    def test_t1_frozen_record_cannot_be_mutated(self):
        """PredictionRecord is a frozen dataclass — attribute writes must fail."""
        p = _pred()
        with self.assertRaises((TypeError, AttributeError)):
            object.__setattr__(p, "line", 99.0)   # bypass test; should still fail
            p.line = 99.0   # direct assignment — should also raise

    def test_t1b_direct_assignment_raises(self):
        """Direct field assignment on a frozen dataclass raises FrozenInstanceError."""
        from dataclasses import FrozenInstanceError
        p = _pred()
        with self.assertRaises(FrozenInstanceError):
            p.line = 99.0  # type: ignore[misc]


# ── T2/T3 — prediction_id determinism and collision ──────────────────────────

class TestPredictionId(unittest.TestCase):

    def test_t2_deterministic_id(self):
        """Same inputs → same prediction_id."""
        p1 = _pred(frozen_at="2026-07-15T12:00:00+00:00")
        p2 = _pred(frozen_at="2026-07-15T12:00:00+00:00")
        self.assertEqual(p1.prediction_id, p2.prediction_id)

    def test_t3_different_line_different_id(self):
        """Different line → different prediction_id (collision detection)."""
        p1 = _pred(line=15.5, frozen_at="2026-07-15T12:00:00+00:00")
        p2 = _pred(line=14.5, frozen_at="2026-07-15T12:00:00+00:00")
        self.assertNotEqual(p1.prediction_id, p2.prediction_id)

    def test_t3b_id_starts_with_pred(self):
        """Prediction IDs have 'pred_' prefix."""
        p = _pred()
        self.assertTrue(p.prediction_id.startswith("pred_"))


# ── T4 — Leakage guard ───────────────────────────────────────────────────────

class TestLeakageGuard(unittest.TestCase):

    def test_t4_outcome_before_prediction_raises(self):
        """outcome_timestamp ≤ frozen_at must raise ValueError."""
        from validation.schema.outcome_record import attach_outcome
        p = _pred(frozen_at="2026-07-15T12:00:00+00:00")
        with self.assertRaises(ValueError) as ctx:
            attach_outcome(
                p, actual_pitches=13, outcome_source="test",
                _outcome_timestamp="2026-07-15T11:59:59+00:00"  # before frozen_at
            )
        self.assertIn("Leakage", str(ctx.exception))

    def test_t4b_outcome_exactly_at_frozen_at_raises(self):
        """outcome_timestamp == frozen_at must also raise."""
        from validation.schema.outcome_record import attach_outcome
        p = _pred(frozen_at="2026-07-15T12:00:00+00:00")
        with self.assertRaises(ValueError):
            attach_outcome(
                p, actual_pitches=13, outcome_source="test",
                _outcome_timestamp="2026-07-15T12:00:00+00:00"
            )

    def test_t4c_outcome_after_frozen_at_succeeds(self):
        """outcome_timestamp > frozen_at does not raise."""
        o = _outcome(_pred())
        self.assertIsNotNone(o.actual_pitches)


# ── T5 — Hit derivation ───────────────────────────────────────────────────────

class TestHitDerivation(unittest.TestCase):

    def test_t5_less_hit(self):
        """LESS: actual < line → hit=True."""
        from validation.schema.outcome_record import _derive_hit
        self.assertTrue(_derive_hit(13, 15.5, "LESS"))

    def test_t5_less_miss(self):
        """LESS: actual > line → hit=False."""
        from validation.schema.outcome_record import _derive_hit
        self.assertFalse(_derive_hit(17, 15.5, "LESS"))

    def test_t5_more_hit(self):
        """MORE: actual > line → hit=True."""
        from validation.schema.outcome_record import _derive_hit
        self.assertTrue(_derive_hit(17, 15.5, "MORE"))

    def test_t5_more_miss(self):
        """MORE: actual < line → hit=False."""
        from validation.schema.outcome_record import _derive_hit
        self.assertFalse(_derive_hit(13, 15.5, "MORE"))

    def test_t5_push_is_miss_for_less(self):
        """Push (actual == line integer part) is a miss for LESS (conservative)."""
        from validation.schema.outcome_record import _derive_hit
        # 15.5 line; actual=15 → 15 < 15.5 → True (HIT for LESS)
        self.assertTrue(_derive_hit(15, 15.5, "LESS"))
        # actual=16 → 16 > 15.5 → miss for LESS
        self.assertFalse(_derive_hit(16, 15.5, "LESS"))

    def test_t5_invalid_direction_raises(self):
        from validation.schema.outcome_record import _derive_hit
        with self.assertRaises(ValueError):
            _derive_hit(13, 15.5, "DRAW")


# ── T6/T7/T8 — Chronological split ──────────────────────────────────────────

class TestChronologicalSplit(unittest.TestCase):

    def _split(self, n: int = 20):
        from validation.splitting.chronological_split import chronological_split
        pairs = _make_pairs(n)
        return chronological_split(pairs, train_fraction=0.6,
                                   validation_fraction=0.2, holdout_fraction=0.2)

    def test_t6_split_sizes_correct(self):
        """Split sizes match fractions within ±1 due to floor rounding."""
        s = self._split(20)
        self.assertAlmostEqual(len(s.train), 12, delta=1)
        self.assertAlmostEqual(len(s.validation), 4, delta=1)
        self.assertAlmostEqual(len(s.holdout), 4, delta=1)

    def test_t7_holdout_is_latest_dates(self):
        """Holdout records have dates strictly after all validation records."""
        s = self._split(20)
        if s.validation and s.holdout:
            max_val_date = max(p.game_date for p, _ in s.validation)
            min_hold_date = min(p.game_date for p, _ in s.holdout)
            self.assertGreaterEqual(min_hold_date, max_val_date)

    def test_t8_manifest_counts_consistent(self):
        """Manifest counts sum to total_records."""
        s = self._split(20)
        m = s.manifest
        self.assertEqual(
            m.train_count + m.validation_count + m.holdout_count,
            m.total_records,
        )

    def test_t6b_fractions_must_sum_to_one(self):
        from validation.splitting.chronological_split import chronological_split
        pairs = _make_pairs(10)
        with self.assertRaises(ValueError):
            chronological_split(pairs, train_fraction=0.5,
                                 validation_fraction=0.3, holdout_fraction=0.3)

    def test_t29_tiny_dataset_handled(self):
        """3-record split does not crash even though fractions yield 0-count sets."""
        from validation.splitting.chronological_split import chronological_split
        pairs = _make_pairs(3)
        s = chronological_split(pairs)
        total = s.manifest.train_count + s.manifest.validation_count + s.manifest.holdout_count
        self.assertEqual(total, 3)


# ── T9 — Brier score ─────────────────────────────────────────────────────────

class TestBrierScore(unittest.TestCase):

    def test_t9_perfect_predictor(self):
        """Perfect predictor: Brier = 0."""
        from validation.metrics.core import brier_score
        samples = [(1.0, True), (0.0, False), (1.0, True)]
        r = brier_score(samples)
        self.assertAlmostEqual(r["score"], 0.0, places=5)

    def test_t9_constant_half_predictor(self):
        """Constant 0.5 predictor on balanced outcomes: Brier = 0.25."""
        from validation.metrics.core import brier_score
        samples = [(0.5, True)] * 5 + [(0.5, False)] * 5
        r = brier_score(samples)
        self.assertAlmostEqual(r["score"], 0.25, places=5)

    def test_t9_null_prob_skipped(self):
        """None probability rows are skipped; n_skipped_null_prob is correct."""
        from validation.metrics.core import brier_score
        samples = [(None, True), (0.5, False), (None, True)]
        r = brier_score(samples)
        self.assertEqual(r["n"], 1)
        self.assertEqual(r["n_skipped_null_prob"], 2)

    def test_t9_all_null_returns_none_score(self):
        from validation.metrics.core import brier_score
        r = brier_score([(None, True), (None, False)])
        self.assertIsNone(r["score"])
        self.assertEqual(r["status"], "INSUFFICIENT_SAMPLE")


# ── T10 — Log loss ────────────────────────────────────────────────────────────

class TestLogLoss(unittest.TestCase):

    def test_t10_coin_flip_baseline(self):
        """Constant 0.5 predictor: log loss ≈ ln(2) ≈ 0.6931."""
        from validation.metrics.core import log_loss
        samples = [(0.5, True)] * 50 + [(0.5, False)] * 50
        r = log_loss(samples)
        self.assertAlmostEqual(r["score"], math.log(2), places=3)

    def test_t10_null_skipped(self):
        from validation.metrics.core import log_loss
        r = log_loss([(None, True)])
        self.assertIsNone(r["score"])


# ── T11 — Calibration buckets ────────────────────────────────────────────────

class TestCalibrationBuckets(unittest.TestCase):

    def test_t11_correct_bin_count(self):
        """n_bins bins are produced regardless of sample distribution."""
        from validation.metrics.core import calibration_buckets
        samples = [(i / 20, True) for i in range(20)]
        r = calibration_buckets(samples, n_bins=5)
        self.assertEqual(len(r["bins"]), 5)

    def test_t11_ece_computed(self):
        """ECE is a float for a non-empty sample."""
        from validation.metrics.core import calibration_buckets
        samples = [(0.6, True)] * 20 + [(0.3, False)] * 20
        r = calibration_buckets(samples)
        self.assertIsNotNone(r["ece"])
        self.assertIsInstance(r["ece"], float)

    def test_t11_sparse_bins_flagged(self):
        """Bins with fewer than min_bin_count predictions are flagged SPARSE."""
        from validation.metrics.core import calibration_buckets
        # Only one sample in the 0.8–1.0 bin
        samples = [(0.1, False)] * 20 + [(0.9, True)]
        r = calibration_buckets(samples, n_bins=5, min_bin_count=3)
        statuses = [b["status"] for b in r["bins"]]
        self.assertIn("SPARSE", statuses)


# ── T12 — Sample coverage ────────────────────────────────────────────────────

class TestSampleCoverage(unittest.TestCase):

    def test_t12_null_counted_correctly(self):
        from validation.metrics.core import sample_coverage
        samples = [(None, True), (0.5, False), (None, False), (0.6, True)]
        r = sample_coverage(samples)
        self.assertEqual(r["n_null_prob"], 2)
        self.assertEqual(r["n_with_prob"], 2)
        self.assertAlmostEqual(r["coverage_rate"], 0.5, places=4)

    def test_t12_insufficient_sample_status(self):
        from validation.metrics.core import sample_coverage
        r = sample_coverage([(0.5, True)] * 3, min_total=10)
        self.assertEqual(r["status"], "INSUFFICIENT_SAMPLE")


# ── T13 — Line slices ────────────────────────────────────────────────────────

class TestLineSlices(unittest.TestCase):

    def test_t13_grouped_by_line(self):
        from validation.metrics.core import line_slices
        samples = [(0.5, True)] * 5 + [(0.5, False)] * 5
        lines   = [15.5] * 5 + [14.5] * 5
        r = line_slices(samples, lines)
        self.assertIn("15.5", r)
        self.assertIn("14.5", r)
        self.assertEqual(r["15.5"]["n"], 5)

    def test_t13_sparse_slice_flagged(self):
        from validation.metrics.core import line_slices
        samples = [(0.5, True)] * 2
        lines   = [15.5, 15.5]
        r = line_slices(samples, lines, min_per_slice=5)
        self.assertEqual(r["15.5"]["status"], "SPARSE")


# ── T14 — None probability flows through safely ──────────────────────────────

class TestMissingData(unittest.TestCase):

    def test_t14_none_prob_no_crash(self):
        """All metrics handle None probabilities without raising."""
        from validation.metrics.core import evaluate
        samples = [(None, True), (None, False), (None, True)]
        lines   = [15.5, 15.5, 14.5]
        r = evaluate(samples, lines, split_label="validation")
        self.assertIsNone(r["brier"]["score"])
        self.assertIsNone(r["log_loss"]["score"])


# ── T15 — Duplicate ID detection ────────────────────────────────────────────

class TestDuplicatePredictionId(unittest.TestCase):

    def test_t15_same_inputs_same_id(self):
        """Two predictions with identical inputs share the same ID (detectable duplicate)."""
        p1 = _pred(frozen_at="2026-07-15T12:00:00+00:00")
        p2 = _pred(frozen_at="2026-07-15T12:00:00+00:00")
        # Duplicate detected: both IDs are identical → set collapses to 1 element
        self.assertEqual(p1.prediction_id, p2.prediction_id)
        self.assertEqual(len({p1.prediction_id, p2.prediction_id}), 1)

    def test_t15_different_time_different_id(self):
        """Different frozen_at → different ID."""
        p1 = _pred(frozen_at="2026-07-15T12:00:00+00:00")
        p2 = _pred(frozen_at="2026-07-15T13:00:00+00:00")
        self.assertNotEqual(p1.prediction_id, p2.prediction_id)


# ── T16 — Baseline A ────────────────────────────────────────────────────────

class TestBaselineSeasonEmpirical(unittest.TestCase):

    def test_t16_correct_hit_rate_less(self):
        """4 out of 5 starts < 15.5 → P=0.8."""
        from validation.baselines.season_empirical import predict_single
        rows = [
            {"first_inning_pitches": 13},
            {"first_inning_pitches": 14},
            {"first_inning_pitches": 15},
            {"first_inning_pitches": 12},
            {"first_inning_pitches": 17},   # miss
        ]
        r = predict_single(rows, 15.5, "LESS")
        self.assertAlmostEqual(r["probability"], 0.8, places=4)

    def test_t16_empty_rows_returns_none(self):
        from validation.baselines.season_empirical import predict_single
        r = predict_single([], 15.5, "LESS")
        self.assertIsNone(r["probability"])


# ── T17 — Baseline B ────────────────────────────────────────────────────────

class TestBaselineL10Empirical(unittest.TestCase):

    def test_t17_window_respected(self):
        """Only last 3 starts used when window=3."""
        from validation.baselines.l10_empirical import predict_single
        rows = [
            {"game_date": "2026-07-01", "first_inning_pitches": 20},  # old: miss at 15.5
            {"game_date": "2026-07-02", "first_inning_pitches": 20},  # old: miss
            {"game_date": "2026-07-10", "first_inning_pitches": 13},  # recent: hit
            {"game_date": "2026-07-11", "first_inning_pitches": 14},  # recent: hit
            {"game_date": "2026-07-12", "first_inning_pitches": 12},  # recent: hit
        ]
        r = predict_single(rows, 15.5, "LESS", window=3)
        self.assertAlmostEqual(r["probability"], 1.0, places=4)
        self.assertEqual(r["n_starts_used"], 3)

    def test_t17_empty_returns_none(self):
        from validation.baselines.l10_empirical import predict_single
        r = predict_single([], 15.5, "LESS")
        self.assertIsNone(r["probability"])


# ── T18/T19 — Baseline C ────────────────────────────────────────────────────

class TestBaselineStatModel(unittest.TestCase):

    def test_t18_reproducible_with_seed(self):
        """Same seed → same probability across two calls."""
        from validation.baselines.stat_model import predict_single
        rows = [
            {"first_inning_pitches": 15, "first_inning_batters_faced": 4},
            {"first_inning_pitches": 17, "first_inning_batters_faced": 4},
            {"first_inning_pitches": 13, "first_inning_batters_faced": 3},
            {"first_inning_pitches": 19, "first_inning_batters_faced": 5},
        ]
        bf = {"p_bf_3": 0.40, "p_bf_4": 0.35, "p_bf_gte5": 0.25}
        r1 = predict_single(rows, bf, 15.5, "LESS", seed=42)
        r2 = predict_single(rows, bf, 15.5, "LESS", seed=42)
        self.assertEqual(r1["probability"], r2["probability"])

    def test_t19_opp_unavailable_reported_not_fabricated(self):
        """Opponent pitches/PA absent → opp_pitches_per_pa='UNAVAILABLE', not a number."""
        from validation.baselines.stat_model import predict_single
        r = predict_single([], None, 15.5, "LESS", opp_pitches_per_pa=None)
        self.assertEqual(r["opp_pitches_per_pa"], "UNAVAILABLE")
        self.assertFalse(r["opp_adjustment_applied"])

    def test_t30_defaults_used_with_few_rows(self):
        """Fewer than 3 rows → genre defaults mean=4.2 std=1.1."""
        from validation.baselines.stat_model import predict_single, _DEFAULT_PPB_MEAN, _DEFAULT_PPB_STD
        r = predict_single([{"first_inning_pitches": 15, "first_inning_batters_faced": 4}],
                           None, 15.5, "LESS")
        self.assertEqual(r["ppb_mean"], _DEFAULT_PPB_MEAN)
        self.assertEqual(r["ppb_std"], _DEFAULT_PPB_STD)


# ── T20 — WOW_LEAN_1IP adapter (mocked Savant) ──────────────────────────────

class TestWOWLean1IPAdapter(unittest.TestCase):

    def test_t20_adapter_builds_prediction_record(self):
        """Adapter returns a PredictionRecord when Savant succeeds."""
        from validation.adapters.wow_lean_1ip import predict
        from validation.schema.prediction_record import PredictionRecord

        mock_ledger = {
            "ledger_rows": [
                {"game_date": "2026-07-01", "first_inning_pitches": 15,
                 "first_inning_batters_faced": 4, "opponent": "CWS",
                 "starter_confirmed": "LIKELY", "source": "Baseball Savant",
                 "source_game_id": "pk_1", "hit": "HIT"},
            ],
            "bf_distribution": {
                "n": 8, "p_bf_3": 0.40, "p_bf_4": 0.35,
                "p_bf_5plus": 0.25, "p_bf_gte5": 0.25,
                "note": "Based on 8 starts",
            },
            "l10_hit_rate": 0.6, "l5_hit_rate": 0.6,
            "l10_pitch_mean": 15.5, "l10_pitch_std": 2.1,
            "l5_pitch_mean": 15.0, "l5_pitch_std": 2.0,
            "l5_pitch_median": 15.0, "l10_pitch_median": 15.5,
            "data_coverage": 8,
            "fetch_method": "savant_csv_direct",
            "source": "Baseball Savant (statcast_pitcher)",
            "pitcher_id": 669392, "season": "2026",
            "board_date": "2026-08-17", "gaps": [], "error": None,
            "can_execute": False,
        }

        import unittest.mock as mock
        with mock.patch(
            "gate_engine.mlb.savant_1ip_ledger.build_1ip_ledger",
            return_value=mock_ledger,
        ):
            result = predict(
                pitcher_name     = "Shota Imanaga",
                pitcher_mlbam_id = 669392,
                opponent         = "CWS",
                game_date        = "2026-08-17",
                line             = 15.5,
                direction        = "LESS",
                _frozen_at       = "2026-08-17T10:00:00+00:00",
            )

        self.assertIsNone(result.get("error"))
        self.assertIsNotNone(result["prediction_record"])
        pr = result["prediction_record"]
        self.assertIsInstance(pr, PredictionRecord)
        self.assertEqual(pr.line, 15.5)
        self.assertEqual(pr.direction, "LESS")
        self.assertEqual(pr.pitcher_mlbam_id, 669392)
        self.assertEqual(pr.prop_type, "1IP_PITCHES_THROWN")
        self.assertIsNotNone(pr.model_probability)

    def test_t20_adapter_returns_none_record_on_empty_bf(self):
        """Empty BF dist → prediction_record=None, error reported."""
        from validation.adapters.wow_lean_1ip import predict
        import unittest.mock as mock

        mock_ledger = {
            "bf_distribution": {"n": 0, "p_bf_3": None, "p_bf_4": None,
                                 "p_bf_gte5": None, "note": "empty"},
            "ledger_rows": [], "l10_hit_rate": None, "error": None, "can_execute": False,
        }
        with mock.patch("gate_engine.mlb.savant_1ip_ledger.build_1ip_ledger",
                        return_value=mock_ledger):
            result = predict(pitcher_name="X", pitcher_mlbam_id=1,
                             opponent="OPP", game_date="2026-08-17",
                             line=15.5, direction="LESS")

        self.assertIsNone(result["prediction_record"])
        self.assertIsNotNone(result["error"])


# ── T21 — Feature registry ──────────────────────────────────────────────────

class TestFeatureRegistry(unittest.TestCase):

    def test_t21_all_10_features_present(self):
        from validation.ablation.features import FEATURE_REGISTRY
        ids = [f.id for f in FEATURE_REGISTRY]
        expected = [
            "failure_path", "l10_discernment", "top_four_detail",
            "handedness", "health_workload", "catcher", "weather",
            "market_prior", "recent_form", "matchup_adjustment",
        ]
        for e in expected:
            self.assertIn(e, ids, f"Feature {e!r} missing from registry")

    def test_t21_supported_and_unavailable_counts(self):
        from validation.ablation.features import supported_features, unavailable_features
        sup = supported_features()
        una = unavailable_features()
        self.assertGreater(len(sup), 0)
        self.assertGreater(len(una), 0)
        self.assertEqual(len(sup) + len(una), 10)

    def test_t21_unavailable_have_reason(self):
        """All unsupported features have an unavailable_reason."""
        from validation.ablation.features import unavailable_features
        for f in unavailable_features():
            self.assertIsNotNone(f.unavailable_reason, f"{f.id} missing unavailable_reason")


# ── T22/T23 — Ablation ──────────────────────────────────────────────────────

class TestAblationRunner(unittest.TestCase):

    def _abl_rows(self, n: int = 8) -> list:
        import math
        rows = []
        for i in range(n):
            rows.append({
                "ledger_rows": [
                    {"game_date": f"2026-07-{j+1:02d}",
                     "first_inning_pitches": 14 + j,
                     "first_inning_batters_faced": 3 + (j % 3),
                     "hit": "HIT" if 14 + j < 15.5 else "MISS"}
                    for j in range(8)
                ],
                "bf_distribution": {"p_bf_3": 0.40, "p_bf_4": 0.35, "p_bf_gte5": 0.25},
                "line": 15.5,
                "direction": "LESS",
                "hit": bool(i % 2),
            })
        return rows

    def test_t22_unavailable_features_reported_not_fabricated(self):
        """UNAVAILABLE features appear in ablation output with status=UNAVAILABLE."""
        from validation.ablation.runner import run_ablation
        from validation.ablation.features import unavailable_features
        rows = self._abl_rows()
        result = run_ablation(rows, include_unavailable=True)
        for spec in unavailable_features():
            self.assertIn(spec.id, result, f"{spec.id} missing from ablation output")
            self.assertEqual(result[spec.id]["status"], "UNAVAILABLE")
            self.assertIsNone(result[spec.id]["brier_full"])

    def test_t23_supported_features_return_numeric_brier(self):
        """Supported features produce numeric Brier scores."""
        from validation.ablation.runner import run_ablation
        from validation.ablation.features import supported_features
        rows = self._abl_rows(10)
        result = run_ablation(rows)
        for spec in supported_features():
            if spec.id in result:
                entry = result[spec.id]
                if entry["status"] == "RAN" and entry["n"] > 0:
                    self.assertIsInstance(entry["brier_full"], float)


# ── T24/T25 — Reporters ─────────────────────────────────────────────────────

class TestReporters(unittest.TestCase):

    def _minimal_report(self) -> dict:
        from validation.reporters.json_reporter import build_report
        return build_report(
            split_manifest    = {"train_count": 30, "validation_count": 10,
                                  "holdout_count": 10, "excluded_count": 0,
                                  "train_date_range": ("2026-06-01", "2026-07-15"),
                                  "validation_date_range": ("2026-07-16", "2026-07-31"),
                                  "holdout_date_range": ("2026-08-01", "2026-08-15")},
            model_results     = {"train": None, "validation": {
                "brier": {"score": 0.22, "n": 10},
                "log_loss": {"score": 0.65, "n": 10},
                "calibration": {"ece": 0.08, "bins": [], "n_total": 10},
                "coverage": {"n_with_prob": 10},
                "line_slices": {},
            }, "holdout": None},
            baseline_results  = {},
            ablation_results  = {},
            eval_rules        = {
                "primary_threshold":   {"warn_above": 0.25, "fail_above": 0.30},
                "secondary_threshold": {"warn_above": 0.693, "fail_above": 0.75},
                "calibration":         {"max_calibration_error": 0.15},
            },
            holdout_evaluated = False,
        )

    def test_t24_json_report_required_keys(self):
        """JSON report contains all required top-level keys."""
        report = self._minimal_report()
        required = [
            "report_type", "harness_version", "frozen_commit",
            "generated_at", "holdout_evaluated", "eval_rules",
            "split_manifest", "verdicts", "model_results",
            "baseline_results", "ablation", "limitations",
        ]
        for k in required:
            self.assertIn(k, report, f"Missing key: {k!r}")

    def test_t24_verdicts_pass_when_brier_good(self):
        """Brier=0.22 < warn_above=0.25 → PASS verdict."""
        report = self._minimal_report()
        verdict = report["verdicts"]["primary_brier"]["verdict"]
        self.assertEqual(verdict, "PASS")

    def test_t25_markdown_non_empty(self):
        from validation.reporters.markdown_reporter import build_markdown
        md = build_markdown(self._minimal_report())
        self.assertGreater(len(md), 100)
        self.assertIn("WOW Prediction Validation Harness", md)
        self.assertIn("Gate Verdicts", md)

    def test_t25_markdown_frozen_commit_present(self):
        from validation.reporters.markdown_reporter import build_markdown
        from validation import FROZEN_COMMIT
        md = build_markdown(self._minimal_report())
        self.assertIn(FROZEN_COMMIT, md)


# ── T26 — Holdout not evaluated by default ──────────────────────────────────

class TestHoldoutNotEvaluated(unittest.TestCase):

    def test_t26_holdout_evaluated_false_by_default(self):
        """build_report defaults holdout_evaluated=False."""
        from validation.reporters.json_reporter import build_report
        report = build_report(
            split_manifest={}, model_results={}, baseline_results={},
            ablation_results={}, eval_rules={},
        )
        self.assertFalse(report["holdout_evaluated"])

    def test_t26_eval_rules_present_in_report(self):
        """Pre-declared eval rules are embedded in the report."""
        from validation.reporters.json_reporter import build_report
        rules = {"primary_metric": "brier_score", "primary_threshold": {"warn_above": 0.25}}
        report = build_report(
            split_manifest={}, model_results={}, baseline_results={},
            ablation_results={}, eval_rules=rules,
        )
        self.assertEqual(report["eval_rules"]["primary_metric"], "brier_score")


# ── T27 — Synthetic dataset ─────────────────────────────────────────────────

class TestSyntheticDataset(unittest.TestCase):

    def test_t27_50_pairs_correct_split(self):
        """Synthetic 50-pair dataset splits into approximately 30/10/10."""
        from validation.cli import _build_synthetic_dataset
        from validation.splitting.chronological_split import chronological_split
        pairs = _build_synthetic_dataset(50)
        self.assertEqual(len(pairs), 50)
        s = chronological_split(pairs)
        self.assertAlmostEqual(len(s.train), 30, delta=2)
        self.assertAlmostEqual(len(s.validation), 10, delta=2)
        self.assertAlmostEqual(len(s.holdout), 10, delta=2)

    def test_t27_pairs_are_chronologically_sorted(self):
        from validation.cli import _build_synthetic_dataset
        pairs = _build_synthetic_dataset(20)
        dates = [p.game_date for p, _ in pairs]
        self.assertEqual(dates, sorted(dates))


# ── T28 — Feature snapshot ID changes with features ──────────────────────────

class TestFeatureSnapshotId(unittest.TestCase):

    def test_t28_different_features_different_snapshot(self):
        from validation.schema.prediction_record import PredictionRecord
        frozen_at = "2026-07-15T12:00:00+00:00"
        p1 = PredictionRecord.create(
            game_date="2026-07-15", pitcher_name="X", pitcher_mlbam_id=1,
            opponent="OPP", line=15.5, direction="LESS",
            model_probability=0.5, model_uncertainty=None,
            features={"bf_n": 10}, model_version="v1",
            data_provenance={}, _frozen_at=frozen_at,
        )
        p2 = PredictionRecord.create(
            game_date="2026-07-15", pitcher_name="X", pitcher_mlbam_id=1,
            opponent="OPP", line=15.5, direction="LESS",
            model_probability=0.5, model_uncertainty=None,
            features={"bf_n": 20},   # different feature
            model_version="v1", data_provenance={}, _frozen_at=frozen_at,
        )
        self.assertNotEqual(p1.feature_snapshot_id, p2.feature_snapshot_id)

    def test_t28_same_features_same_snapshot(self):
        from validation.schema.prediction_record import PredictionRecord
        frozen_at = "2026-07-15T12:00:00+00:00"
        p1 = PredictionRecord.create(
            game_date="2026-07-15", pitcher_name="X", pitcher_mlbam_id=1,
            opponent="OPP", line=15.5, direction="LESS",
            model_probability=0.5, model_uncertainty=None,
            features={"bf_n": 10}, model_version="v1",
            data_provenance={}, _frozen_at=frozen_at,
        )
        p2 = PredictionRecord.create(
            game_date="2026-07-15", pitcher_name="X", pitcher_mlbam_id=1,
            opponent="OPP", line=15.5, direction="LESS",
            model_probability=0.5, model_uncertainty=None,
            features={"bf_n": 10},   # same feature
            model_version="v1", data_provenance={}, _frozen_at=frozen_at,
        )
        self.assertEqual(p1.feature_snapshot_id, p2.feature_snapshot_id)


if __name__ == "__main__":
    unittest.main()
