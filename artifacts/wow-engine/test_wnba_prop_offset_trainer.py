import numpy as np

from scripts import train_wnba_props as base
from scripts import train_wnba_props_offset as trainer


def test_correction_design_is_target_blind_and_finite():
    x = np.asarray([
        [10.0, 12.0, 14.0, 30.0, 32.0, 34.0],
        [0.0, 0.0, 1.0, 20.0, 20.0, 21.0],
    ])
    baseline, design = trainer.correction_design(x)
    assert baseline.shape == (2,)
    assert design.shape == (2, 4)
    assert np.isfinite(baseline).all()
    assert np.isfinite(design).all()
    assert baseline[0] == 10.0
    assert baseline[1] == 0.05


def test_offset_glm_and_prediction_are_finite():
    rng = np.random.default_rng(7)
    l10 = rng.uniform(1.0, 20.0, size=500)
    x = np.column_stack([
        l10,
        l10 * rng.uniform(0.85, 1.15, size=500),
        l10 * rng.uniform(0.75, 1.25, size=500),
        rng.uniform(20.0, 35.0, size=500),
        rng.uniform(20.0, 35.0, size=500),
        rng.uniform(18.0, 38.0, size=500),
    ])
    y = rng.poisson(np.maximum(l10 * 1.03, 0.05))
    intercept, coef = trainer.fit_offset_glm(x, y)
    pred = trainer.offset_predict(x, intercept, coef)
    assert np.isfinite(intercept)
    assert coef.shape == (4,)
    assert np.isfinite(coef).all()
    assert np.isfinite(pred).all()
    assert (pred > 0).all()


def test_inner_model_selection_never_collapses_to_zero_weight():
    rows = []
    for day in range(1, 31):
        for player in range(20):
            l10 = 5.0 + (player % 5)
            features = (l10, l10 + 0.2, l10 + 0.1, 30.0, 31.0, 31.5)
            rows.append(
                base.FeaturedRow(
                    player_id=str(player),
                    game_id=f"g-{day}-{player}",
                    game_date=f"2026-06-{day:02d}",
                    features=features,
                    actual=int(round(l10)),
                )
            )
    weight, meta = trainer.choose_blend_weight(rows)
    assert trainer.MIN_GLM_BLEND_WEIGHT <= weight <= 1.0
    assert meta["selected_blend_weight"] == weight
    assert meta["inner_train_rows"] >= 200
    assert meta["inner_validation_rows"] >= 100
