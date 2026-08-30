from agent_runtime.reducer import RequiredJobResult, reduce_candidate


def _clean_required_jobs():
    return [
        RequiredJobResult("wow.slate-integrity-expert", "SUCCEEDED", "IDENTITY_VERIFIED"),
        RequiredJobResult("wow.evidence-hydration", "SUCCEEDED", "EVIDENCE_VERIFIED"),
        RequiredJobResult("wow.controlling-model", "SUCCEEDED", "MODEL_QUALIFIED_HOLD"),
        RequiredJobResult("wow.failure-path-framework", "SUCCEEDED", "MODEL_QUALIFIED_HOLD"),
        RequiredJobResult("wow.dynamic-calibration-expert", "SUCCEEDED", "MODEL_QUALIFIED_HOLD"),
        RequiredJobResult("wow.exact-line-market-auditor", "SUCCEEDED", "MARKET_VERIFIED_HOLD"),
        RequiredJobResult("wow.structure-exposure-governor", "SUCCEEDED", "STRUCTURE_VERIFIED_HOLD"),
        RequiredJobResult("wow.final-refresh-governor", "SUCCEEDED", "FINAL_REFRESH_HOLD"),
    ]


def test_successful_stage_authority_ceilings_do_not_pin_candidate():
    decision = reduce_candidate(
        controlling_worker_id="wow.controlling-model",
        controlling_job_status="SUCCEEDED",
        required_jobs=_clean_required_jobs(),
    )

    assert decision.label == "FINAL_APPROVED"
    assert decision.ceiling == "FINAL_APPROVED"
    assert decision.blockers == ()
    assert decision.probability_publishable is True
    assert decision.can_execute is False


def test_real_publication_blocker_caps_candidate_at_model_hold():
    jobs = _clean_required_jobs()
    jobs[2] = RequiredJobResult(
        "wow.controlling-model",
        "SUCCEEDED",
        "MODEL_QUALIFIED_HOLD",
        ("PROBABILITY_PUBLICATION_HELD",),
    )

    decision = reduce_candidate(
        controlling_worker_id="wow.controlling-model",
        controlling_job_status="SUCCEEDED",
        required_jobs=jobs,
    )

    assert decision.label == "MODEL_QUALIFIED_HOLD"
    assert decision.ceiling == "MODEL_QUALIFIED_HOLD"
    assert decision.blockers == ("PROBABILITY_PUBLICATION_HELD",)
    assert decision.probability_publishable is False
    assert decision.can_execute is False


def test_unknown_ceiling_fails_closed_even_on_successful_stage():
    jobs = _clean_required_jobs()
    jobs[0] = RequiredJobResult(
        "wow.slate-integrity-expert",
        "SUCCEEDED",
        "UNRATIFIED_NEW_LABEL",
    )

    decision = reduce_candidate(
        controlling_worker_id="wow.controlling-model",
        controlling_job_status="SUCCEEDED",
        required_jobs=jobs,
    )

    assert decision.label == "GOVERNANCE_LABEL_UNKNOWN"
    assert decision.ceiling == "GOVERNANCE_LABEL_UNKNOWN"
    assert decision.probability_publishable is False
    assert decision.can_execute is False


def test_missing_mandatory_stage_cannot_approve():
    jobs = [
        job for job in _clean_required_jobs()
        if job.worker_id != "wow.dynamic-calibration-expert"
    ]

    decision = reduce_candidate(
        controlling_worker_id="wow.controlling-model",
        controlling_job_status="SUCCEEDED",
        required_jobs=jobs,
    )

    assert decision.label == "RESEARCH_INTEREST"
    assert decision.ceiling == "RESEARCH_INTEREST"
    assert "MANDATORY_STAGE_MISSING:wow.dynamic-calibration-expert" in decision.blockers
    assert decision.probability_publishable is False
    assert decision.can_execute is False


def test_failed_controlling_specialist_remains_model_unavailable():
    jobs = _clean_required_jobs()
    jobs[2] = RequiredJobResult(
        "wow.controlling-model",
        "BLOCKED",
        "RESEARCH_INTEREST",
        ("MODEL_UNAVAILABLE",),
    )

    decision = reduce_candidate(
        controlling_worker_id="wow.controlling-model",
        controlling_job_status="BLOCKED",
        required_jobs=jobs,
    )

    assert decision.label == "MODEL_UNAVAILABLE"
    assert decision.ceiling == "MODEL_UNAVAILABLE"
    assert decision.probability_publishable is False
    assert decision.can_execute is False
