"""Fit and fail-closed tests for NHL explicit-regime goal-count research model."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import math
import pytest

from v17.nhl_total_goals_regime_mixture_research import (
    CAN_EXECUTE, PROBABILITY_PUBLISHABLE, RANK_ELIGIBLE,
    REGIMES, NHLRegimeMixtureResearchError,
    RegimeResearchRow, fit_regime_mixture_research, research_exact_line,
    research_pmf_at,
)


def fixture_rows():
    rows = []
    for year in (2021, 2022, 2023, 2024, 2025):
        for i in range(180):
            regime = REGIMES[i % 3]
            start = datetime(year, 10, 1, tzinfo=timezone.utc) + timedelta(minutes=i*12)
            total = (3 + (i*7+year) % 9) if regime == "REG" else 3+2*((i//3+year)%5)
            features = (
                float((i*2+year)%11), float((i+year)%12),
                float((i*3+year)%9), float((i+2*year)%13),
                float((i%5)+1), float(((i+1)%5)+1),
            )
            rows.append(RegimeResearchRow(
                event_id=f"{year}-{i}", season_start_year=year,
                start_utc=start.isoformat(),
                feature_as_of=(start-timedelta(minutes=1)).isoformat(),
                features=features, total_goals=total, terminal_regime=regime,
                official_source_sha256="a"*64,
                outcome_regime_sha256="b"*64,
            ))
    return rows


def test_fit_produces_real_numeric_components_not_governed_probability():
    artifact, receipt = fit_regime_mixture_research(fixture_rows())
    assert artifact.train_n == 360
    assert artifact.calibration_n == 180
    assert artifact.development_n == 180
    assert receipt["forward_reserve_not_used"] == 180
    assert set(artifact.logistic_class_order) == set(REGIMES)
    assert len(artifact.poisson_coef) == 3
    assert all(v > 0 for v in artifact.poisson_mean_multipliers)
    assert len(artifact.training_hash) == 64
    assert 0 < receipt["research_development_under55_brier"] < 1
    assert 0 < receipt["research_development_exact_six_brier"] < 1
    assert receipt["research_development_count_nll"] > 0
    assert receipt["goalie_fitted"] is False
    assert receipt["calibrated_lower_bound"] is None
    assert receipt["probability_publishable"] is False
    assert artifact.probability_publishable is False
    assert not (CAN_EXECUTE or PROBABILITY_PUBLISHABLE or RANK_ELIGIBLE)


def test_exact_5_5_and_6_0_outcome_arithmetic_preserves_push():
    artifact, _ = fit_regime_mixture_research(fixture_rows())
    x = fixture_rows()[0].features
    for threshold in (4.5, 5.5, 6.0, 6.5, 7.0):
        u = research_exact_line(artifact,x,line=threshold,side="UNDER")
        o = research_exact_line(artifact,x,line=threshold,side="OVER")
        assert u["research_win_probability"] == pytest.approx(o["research_loss_probability"])
        assert u["research_loss_probability"] == pytest.approx(o["research_win_probability"])
        assert u["research_push_probability"] == pytest.approx(o["research_push_probability"])
        assert sum(u[k] for k in ("research_win_probability","research_loss_probability","research_push_probability")) == pytest.approx(1.0)
        assert u["research_push_probability"] >= 0
        assert u["calibrated_probability"] is None
        assert u["certified_model_probability"] is None
        assert u["can_execute"] is False
    assert research_exact_line(artifact,x,line=5.5,side="UNDER")["research_push_probability"] == 0
    six = research_exact_line(artifact,x,line=6.0,side="UNDER")
    assert six["research_push_probability"] > 0


def test_infinite_tail_not_renormalized_or_cut_at_sportsbook_line():
    artifact,_ = fit_regime_mixture_research(fixture_rows())
    features = fixture_rows()[1].features
    assert research_pmf_at(artifact, features, 0) == 0.0
    mass = sum(research_pmf_at(artifact, features, k) for k in range(80))
    assert mass == pytest.approx(1.0, abs=1e-8)
    exactly_six = research_pmf_at(artifact, features, 6)
    six_line = research_exact_line(artifact, features, line=6, side="UNDER")
    assert six_line["research_push_probability"] == pytest.approx(exactly_six)


def test_past_outcome_after_kickoff_is_rejected_as_feature():
    rows = fixture_rows()
    rows[0] = replace(rows[0],feature_as_of=rows[0].start_utc)
    with pytest.raises(NHLRegimeMixtureResearchError) as e:
        fit_regime_mixture_research(rows)
    assert e.value.code == "NHL_REGIME_FEATURE_CHRONOLOGY_INVALID"


def test_duplicate_event_is_not_double_counted_in_training():
    rows = fixture_rows()
    with pytest.raises(NHLRegimeMixtureResearchError) as e:
        fit_regime_mixture_research(rows+[rows[0]])
    assert e.value.code == "NHL_REGIME_DUPLICATE_EVENT"


def test_regime_parity_contradiction_cannot_be_fitted():
    rows = fixture_rows()
    idx = next(i for i,r in enumerate(rows) if r.terminal_regime == "SO")
    rows[idx] = replace(rows[idx],total_goals=6)
    with pytest.raises(NHLRegimeMixtureResearchError) as e:
        fit_regime_mixture_research(rows)
    assert e.value.code == "NHL_REGIME_OUTCOME_INVALID"


def test_unverified_source_hash_rejected():
    rows=fixture_rows()
    rows[0]=replace(rows[0],outcome_regime_sha256="unverified")
    with pytest.raises(NHLRegimeMixtureResearchError) as e:
        fit_regime_mixture_research(rows)
    assert e.value.code == "NHL_REGIME_SOURCE_HASH_INVALID"


def test_short_holdouts_cannot_be_labeled_fitted():
    rows=[r for r in fixture_rows() if r.season_start_year in (2021,2022)]
    with pytest.raises(NHLRegimeMixtureResearchError) as e:
        fit_regime_mixture_research(rows)
    assert e.value.code == "NHL_REGIME_HOLDOUT_INSUFFICIENT"


@pytest.mark.parametrize("line,side", [(5.25,"UNDER"),(6.0,"MORE"),(-1.0,"UNDER"),(True,"UNDER")])
def test_unsupported_exact_thresholds_fail_closed(line,side):
    artifact,_=fit_regime_mixture_research(fixture_rows())
    with pytest.raises(NHLRegimeMixtureResearchError):
        research_exact_line(artifact,fixture_rows()[0].features,line=line,side=side)
