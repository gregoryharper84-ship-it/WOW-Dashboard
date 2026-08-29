from agent_runtime.reducer import (
    CEILING_ORDER,
    RequiredJobResult,
    reduce_candidate,
    strictest,
)


def test_strictest_picks_lowest_rank_regardless_of_input_order():
    assert strictest(["WATCH", "VERIFIED", "REJECT"]) == "REJECT"
    assert strictest(["FINAL_APPROVED", "WATCH"]) == "WATCH"


def test_strictest_treats_unknown_ceiling_as_maximally_strict():
    assert strictest(["VERIFIED", "SOME_UNRATIFIED_LABEL"]) == "SOME_UNRATIFIED_LABEL"


def test_strictest_empty_list_fails_closed():
    assert strictest([]) == "GOVERNANCE_LABEL_UNKNOWN"


def test_ceiling_order_has_no_duplicates():
    assert len(CEILING_ORDER) == len(set(CEILING_ORDER))


def test_run_not_terminal_blocks_any_decision():
    decision = reduce_candidate(
        controlling_worker_id="wow.mlb-strikeout-expert",
        controlling_job_status="SUCCEEDED",
        required_jobs=[
            RequiredJobResult(worker_id="wow.slate-integrity-expert", status="SUCCEEDED", ceiling="VERIFIED"),
            RequiredJobResult(worker_id="wow.final-refresh-governor", status="RUNNING"),
        ],
    )
    assert decision.label == "RUN_NOT_TERMINAL"
    assert decision.probability_publishable is False


def test_no_controlling_worker_is_no_specialist_coverage():
    decision = reduce_candidate(
        controlling_worker_id=None,
        controlling_job_status=None,
        required_jobs=[RequiredJobResult(worker_id="wow.parallel-discovery-router", status="SUCCEEDED", ceiling="RESEARCH_INTEREST")],
    )
    assert decision.label == "NO_SPECIALIST_COVERAGE"
    assert decision.probability_publishable is False


def test_controlling_job_not_succeeded_is_model_unavailable_even_if_others_passed():
    decision = reduce_candidate(
        controlling_worker_id="wow.mlb-strikeout-expert",
        controlling_job_status="BLOCKED",
        required_jobs=[
            RequiredJobResult(worker_id="wow.slate-integrity-expert", status="SUCCEEDED", ceiling="VERIFIED"),
            RequiredJobResult(worker_id="wow.mlb-strikeout-expert", status="BLOCKED", ceiling="MODEL_UNAVAILABLE"),
        ],
    )
    assert decision.label == "MODEL_UNAVAILABLE"
    assert decision.probability_publishable is False


def test_downstream_success_cannot_overwrite_an_upstream_block():
    # Packet acceptance fixture #14: one upstream job blocked at HOLD, a
    # downstream job that otherwise looks great must not push the ceiling
    # past what the upstream blocker allows.
    decision = reduce_candidate(
        controlling_worker_id="wow.mlb-strikeout-expert",
        controlling_job_status="SUCCEEDED",
        required_jobs=[
            RequiredJobResult(worker_id="wow.mlb-strikeout-expert", status="SUCCEEDED", ceiling="MODEL_QUALIFIED_HOLD"),
            RequiredJobResult(worker_id="wow.exact-line-market-auditor", status="SUCCEEDED", ceiling="MARKET_VERIFIED"),
        ],
    )
    assert decision.ceiling == "MODEL_QUALIFIED_HOLD"
    assert decision.probability_publishable is False


def test_all_required_jobs_clean_and_verified_is_publishable():
    decision = reduce_candidate(
        controlling_worker_id="wow.mlb-strikeout-expert",
        controlling_job_status="SUCCEEDED",
        required_jobs=[
            RequiredJobResult(worker_id="wow.mlb-strikeout-expert", status="SUCCEEDED", ceiling="VERIFIED"),
            RequiredJobResult(worker_id="wow.final-refresh-governor", status="SUCCEEDED", ceiling="FINAL_APPROVED"),
        ],
    )
    assert decision.ceiling == "VERIFIED"
    assert decision.probability_publishable is True
    assert decision.can_execute is False


def test_blockers_are_unioned_and_deduplicated_preserving_first_seen_order():
    decision = reduce_candidate(
        controlling_worker_id="wow.mlb-strikeout-expert",
        controlling_job_status="SUCCEEDED",
        required_jobs=[
            RequiredJobResult(worker_id="a", status="SUCCEEDED", ceiling="WATCH", blockers=("STALE_MARKET",)),
            RequiredJobResult(worker_id="b", status="SUCCEEDED", ceiling="WATCH", blockers=("STALE_MARKET", "NO_VIG_MISMATCH")),
        ],
    )
    assert decision.blockers == ("STALE_MARKET", "NO_VIG_MISMATCH")


def test_unknown_ceiling_fails_closed_as_governance_label_unknown():
    decision = reduce_candidate(
        controlling_worker_id="wow.mlb-strikeout-expert",
        controlling_job_status="SUCCEEDED",
        required_jobs=[
            RequiredJobResult(worker_id="a", status="SUCCEEDED", ceiling="TOTALLY_MADE_UP_LABEL"),
        ],
    )
    assert decision.label == "GOVERNANCE_LABEL_UNKNOWN"
    assert decision.probability_publishable is False
