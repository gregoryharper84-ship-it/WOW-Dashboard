"""Shared champion-vs-challenger evaluator: evidence only, no promotion."""
from __future__ import annotations

import json
import math
import random

import pytest

from v17 import challenger_evaluator as ev

T0, T1 = "2026-10-01T18:00:00Z", "2026-10-01T23:00:00Z"
INERT = ("can_execute", "probability_publishable", "automatic_promotion", "promotion_attempted")


def _row(i, *, cohort=ev.HISTORICAL, inc=0.6, ch=0.6, y=1, family="NBA_WINNER", **extra):
    return {"row_id": f"r{i}", "market_family": family, "cohort": cohort, "incumbent_p": inc,
            "challenger_p": ch, "outcome": y, "forecast_at": T0, "settled_at": T1, **extra}


def _synthetic(n, *, cohort, seed, challenger_sharper=True):
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        true_p = rng.uniform(0.2, 0.8)
        y = 1 if rng.random() < true_p else 0
        noisy = min(0.97, max(0.03, true_p + rng.uniform(-0.25, 0.25)))
        inc, ch = (noisy, true_p) if challenger_sharper else (true_p, noisy)
        rows.append(_row(f"{cohort[:1]}{i}", cohort=cohort, inc=inc, ch=ch, y=y,
                         incumbent_lower=max(0.01, inc - 0.1), challenger_lower=max(0.01, ch - 0.1)))
    return rows


# --- input validation fails closed ----------------------------------------


@pytest.mark.parametrize(
    "patch",
    [
        {"incumbent_p": 0.0}, {"incumbent_p": 1.0}, {"challenger_p": float("nan")}, {"challenger_p": True},
        {"challenger_p": "0.6"}, {"outcome": None}, {"outcome": 2}, {"outcome": True},
        {"cohort": "MIXED"}, {"forecast_at": T1, "settled_at": T0}, {"forecast_at": "2026-10-01T18:00:00"},
        {"market_family": ""}, {"row_id": ""}, {"incumbent_lower": 1.2},
    ],
)
def test_invalid_rows_raise(patch):
    with pytest.raises(ev.EvaluatorInputError):
        ev.evaluate([{**_row(1), **patch}])


def test_duplicate_ids_mixed_families_and_empty_raise():
    with pytest.raises(ev.EvaluatorInputError):
        ev.evaluate([_row(1), _row(1)])
    with pytest.raises(ev.EvaluatorInputError):
        ev.evaluate([_row(1), _row(2, family="NHL_WINNER")])
    with pytest.raises(ev.EvaluatorInputError):
        ev.evaluate([])


# --- evidence packet -------------------------------------------------------


def test_no_policy_means_shadow_only_and_inert():
    packet = ev.evaluate(_synthetic(200, cohort=ev.HISTORICAL, seed=1))
    assert packet["recommendation"] == ev.NO_POLICY
    assert packet["policy"] == {"status": "NONE"}
    assert all(packet[k] is False for k in INERT)
    assert packet["cohorts"][ev.PROSPECTIVE] == {"n": 0, "status": "EMPTY"}


def test_cohorts_are_never_pooled():
    hist = _synthetic(150, cohort=ev.HISTORICAL, seed=2)
    pros = _synthetic(80, cohort=ev.PROSPECTIVE, seed=3, challenger_sharper=False)
    both = ev.evaluate(hist + pros)["cohorts"]
    assert both[ev.HISTORICAL] == ev.evaluate(hist)["cohorts"][ev.HISTORICAL]
    assert both[ev.PROSPECTIVE] == ev.evaluate(pros)["cohorts"][ev.PROSPECTIVE]
    assert both[ev.HISTORICAL]["n"] == 150 and both[ev.PROSPECTIVE]["n"] == 80


def test_sharper_challenger_shows_negative_paired_diff_and_is_deterministic():
    rows = _synthetic(400, cohort=ev.HISTORICAL, seed=4)
    a, b = ev.evaluate(rows), ev.evaluate(list(reversed(rows)))
    assert a == b
    diff = a["cohorts"][ev.HISTORICAL]["brier"]["paired_diff"]
    assert diff["mean"] < 0 and diff["ci_high"] < 0
    assert a["cohorts"][ev.HISTORICAL]["log_loss"]["paired_diff"]["mean"] < 0


def test_metric_values_match_hand_computation():
    rows = [_row(1, inc=0.8, ch=0.6, y=1), _row(2, inc=0.3, ch=0.4, y=0)]
    c = ev.evaluate(rows)["cohorts"][ev.HISTORICAL]
    assert c["brier"]["incumbent"] == pytest.approx((0.04 + 0.09) / 2)
    assert c["brier"]["challenger"] == pytest.approx((0.16 + 0.16) / 2)
    assert c["log_loss"]["incumbent"] == pytest.approx(-(math.log(0.8) + math.log(0.7)) / 2)
    assert c["calibration"]["incumbent"]["bias"] == pytest.approx(0.55 - 0.5)


def test_harm_counts_flips_of_previously_correct_rows():
    rows = [
        _row(1, inc=0.7, ch=0.4, y=1),   # incumbent right, challenger flips wrong -> harmed
        _row(2, inc=0.7, ch=0.6, y=1),   # both right, challenger less confident
        _row(3, inc=0.4, ch=0.7, y=1),   # incumbent wrong, challenger fixes
        _row(4, inc=0.2, ch=0.3, y=0),   # both right
    ]
    harm = ev.evaluate(rows)["cohorts"][ev.HISTORICAL]["harm"]
    assert harm["incumbent_correct_rows"] == 3
    assert harm["flipped_to_wrong"] == 1
    assert harm["flipped_to_wrong_rate"] == pytest.approx(1 / 3)
    assert harm["worse_log_loss_on_correct_rows"] == 3
    assert harm["wrong_rows_fixed"] == 1
    assert harm["harmed_row_ids"] == ["r1"]


def test_lower_bound_shortfall_is_reported():
    rows = [_row(i, inc=0.8, ch=0.8, y=0 if i < 6 else 1, incumbent_lower=0.75) for i in range(10)]
    lb = ev.evaluate(rows)["cohorts"][ev.HISTORICAL]["lower_bound"]
    assert lb["incumbent"]["hit_rate"] == pytest.approx(0.4)
    assert lb["incumbent"]["margin"] == pytest.approx(0.4 - 0.75)
    assert lb["incumbent"]["shortfall_bins"][0]["bin"] == 7
    assert lb["challenger"] == {"n": 0, "status": "NO_LOWER_BOUNDS"}


# --- policy application never promotes -------------------------------------


def _approved(**over):
    policy = {
        "status": "APPROVED", "policy_id": "nba-winner-v1", "approved_by": "governance",
        "market_family": "NBA_WINNER", "min_prospective_n": 50,
        "criteria": [{"cohort": ev.PROSPECTIVE, "metric": "brier.paired_diff.ci_high", "op": "<", "value": 0.0}],
    }
    policy.update(over)
    return policy


def test_unapproved_or_incomplete_policy_is_ignored():
    rows = _synthetic(100, cohort=ev.PROSPECTIVE, seed=5)
    for policy in (_approved(status="PROPOSED_UNAPPROVED"), _approved(approved_by=""), _approved(policy_id=None)):
        assert ev.evaluate(rows, policy=policy)["recommendation"] == ev.NO_POLICY
    for policy, err in ((_approved(market_family="NHL_WINNER"), "MARKET_FAMILY_MISMATCH"),
                        (_approved(min_prospective_n=None), "MIN_PROSPECTIVE_N_MISSING"),
                        (_approved(criteria=[]), "NO_CRITERIA")):
        packet = ev.evaluate(rows, policy=policy)
        assert packet["recommendation"] == ev.NO_POLICY
        assert packet["policy"]["error"] == err


def test_approved_policy_recommends_but_never_promotes():
    hist_only = ev.evaluate(_synthetic(300, cohort=ev.HISTORICAL, seed=6), policy=_approved())
    assert hist_only["recommendation"] == "EXTEND_SHADOW"  # historical replay alone never suffices

    good = ev.evaluate(_synthetic(400, cohort=ev.PROSPECTIVE, seed=7), policy=_approved())
    assert good["recommendation"] == "PROMOTE_RECOMMENDED_FOR_GOVERNED_REVIEW"
    bad = ev.evaluate(_synthetic(400, cohort=ev.PROSPECTIVE, seed=8, challenger_sharper=False), policy=_approved())
    assert bad["recommendation"] == "REJECT_RECOMMENDED"
    for packet in (hist_only, good, bad):
        assert all(packet[k] is False for k in INERT)


def test_missing_metric_fails_the_criterion():
    policy = _approved(criteria=[{"cohort": ev.PROSPECTIVE, "metric": "lower_bound.challenger.margin", "op": ">=", "value": 0.0}])
    rows = [_row(i, cohort=ev.PROSPECTIVE, inc=0.6, ch=0.7, y=1) for i in range(60)]  # no lower bounds
    packet = ev.evaluate(rows, policy=policy)
    assert packet["recommendation"] == "REJECT_RECOMMENDED"
    assert packet["criteria_results"][0]["observed"] is None


# --- proposals are derived and inert ---------------------------------------


def test_proposal_is_derived_from_incumbent_history_and_inert():
    packet = ev.evaluate(_synthetic(300, cohort=ev.HISTORICAL, seed=9))
    proposal = ev.propose_policy(packet)
    hist = packet["cohorts"][ev.HISTORICAL]
    assert proposal["status"] == "PROPOSED_UNAPPROVED"
    assert all(proposal[k] is False for k in INERT)
    ece = next(c for c in proposal["criteria"] if c["metric"] == "calibration.challenger.ece")
    assert ece["value"] == hist["calibration"]["incumbent"]["ece"]
    diff = hist["brier"]["paired_diff"]
    expected = math.ceil(((1.959963984540054 + 0.8416212335729143) * diff["sd"] / abs(diff["mean"])) ** 2)
    assert proposal["min_prospective_n"] == expected
    assert all(c["cohort"] == ev.PROSPECTIVE for c in proposal["criteria"])
    # A proposal fed back as a policy has no effect until approved.
    assert ev.evaluate(_synthetic(400, cohort=ev.PROSPECTIVE, seed=10),
                       policy={**proposal, "policy_id": "x"})["recommendation"] == ev.NO_POLICY


def test_proposal_needs_historical_evidence():
    packet = ev.evaluate(_synthetic(50, cohort=ev.PROSPECTIVE, seed=11))
    assert ev.propose_policy(packet)["status"] == "INSUFFICIENT_HISTORICAL_EVIDENCE"


def test_cli(tmp_path, capsys):
    rows = tmp_path / "rows.jsonl"
    rows.write_text("\n".join(json.dumps(r) for r in _synthetic(100, cohort=ev.HISTORICAL, seed=12)))
    assert ev.main(["--rows-jsonl", str(rows), "--propose"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["recommendation"] == ev.NO_POLICY and out["proposed_policy"]["status"] == "PROPOSED_UNAPPROVED"
    rows.write_text(json.dumps(_row(1, incumbent_p=1.0)))
    assert ev.main(["--rows-jsonl", str(rows)]) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "EVALUATOR_INPUT_INVALID"
