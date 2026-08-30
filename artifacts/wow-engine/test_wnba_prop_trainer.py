import numpy as np

from scripts import train_wnba_props as trainer


def test_feature_builder_is_strictly_prior_and_l10_bounded():
    games = [
        trainer.Game("p1", f"g{i:02d}", f"2026-05-{i+1:02d}", 20.0 + i, i)
        for i in range(12)
    ]
    rows = trainer.build_featured_rows(games)
    assert len(rows) == 2
    first = rows[0]
    assert first.actual == 10
    assert first.features[0] == np.mean(range(10))
    assert first.features[1] == np.mean(range(5, 10))
    assert first.features[2] == 9
    assert first.features[3] == np.mean([20 + i for i in range(10)])
    assert first.features[4] == np.mean([20 + i for i in range(5, 10)])
    assert first.features[5] == 29


def test_temporal_split_has_strict_boundary():
    rows = []
    for d in range(1, 31):
        for p in range(20):
            rows.append(
                trainer.FeaturedRow(
                    str(p),
                    f"g{d}-{p}",
                    f"2026-06-{d:02d}",
                    (1.0, 2.0, 3.0, 20.0, 21.0, 22.0),
                    3,
                )
            )
    train, holdout, cutoff = trainer._split(rows)
    assert max(r.game_date for r in train) < min(r.game_date for r in holdout)
    assert min(r.game_date for r in holdout) == cutoff


def test_poisson_pmf_normalizes_and_folds_tail():
    pmf = trainer.poisson_pmf(12.0, 25)
    assert abs(sum(pmf.values()) - 1.0) < 1e-12
    assert set(pmf) == set(range(26))
    assert pmf[25] > 0


def test_stat_routes_are_external_canonical_names_and_never_execute():
    assert set(trainer.STAT_ROUTES) == {
        "POINTS",
        "REBOUNDS",
        "ASSISTS",
        "THREE_POINTERS_MADE",
    }
    assert trainer.CAN_EXECUTE is False
