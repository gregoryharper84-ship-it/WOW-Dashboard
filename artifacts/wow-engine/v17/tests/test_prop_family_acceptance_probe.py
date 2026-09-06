from prop_family_acceptance_probe import classify_family_acceptance, summarize_rows


def test_no_evidence_is_typed_acquisition_blocker():
    result = classify_family_acceptance(
        stat_type="PITCHING_OUTS",
        evidence_pass_n=0,
        prediction_n=0,
        publishable_n=0,
        bounded_n=0,
    )
    assert result.status == "BLOCKED_NO_PASS_EVIDENCE"


def test_evidence_without_prediction_is_not_production_proven():
    result = classify_family_acceptance(
        stat_type="PITCHING_OUTS",
        evidence_pass_n=4,
        prediction_n=0,
        publishable_n=0,
        bounded_n=0,
    )
    assert result.status == "BLOCKED_NO_GOVERNED_PREDICTION"


def test_prediction_without_publishable_bounds_remains_blocked():
    result = classify_family_acceptance(
        stat_type="PITCHING_OUTS",
        evidence_pass_n=4,
        prediction_n=4,
        publishable_n=0,
        bounded_n=0,
    )
    assert result.status == "BLOCKED_NO_PUBLISHABLE_BOUNDED_OUTPUT"


def test_only_complete_ledger_path_is_production_proven():
    result = classify_family_acceptance(
        stat_type="PITCHER_STRIKEOUTS",
        evidence_pass_n=138,
        prediction_n=229,
        publishable_n=223,
        bounded_n=223,
    )
    assert result.status == "PRODUCTION_PROVEN"


def test_invalid_counts_fail_closed():
    try:
        classify_family_acceptance(
            stat_type="BALLS_THROWN",
            evidence_pass_n=-1,
            prediction_n=0,
            publishable_n=0,
            bounded_n=0,
        )
    except ValueError as exc:
        assert "non-negative" in str(exc)
    else:
        raise AssertionError("negative counts must fail closed")


def test_summarize_rows_defaults_missing_counts_to_zero():
    summary = summarize_rows([{"stat_type": "PLATE_APPEARANCES"}])
    assert summary["PLATE_APPEARANCES"].status == "BLOCKED_NO_PASS_EVIDENCE"
