from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
DEPLOY_WORKFLOW = REPO_ROOT / ".github/workflows/wow-v17-render-production-deploy.yml"
ORCHESTRATOR_WORKFLOW = REPO_ROOT / ".github/workflows/wow-v17-post-deploy-verification-orchestrator.yml"
CANARY_WORKFLOW = REPO_ROOT / ".github/workflows/wow-v17-spread-forward-production-canary.yml"
CERT_WORKFLOW = REPO_ROOT / ".github/workflows/wow-v17-spread-certification-replay.yml"
PRIORITY_WORKFLOW = REPO_ROOT / ".github/workflows/wow-v17-priority-prop-lifecycle.yml"
DAILY_WORKFLOW = REPO_ROOT / ".github/workflows/wow-v17-daily-snapshot.yml"


def test_deploy_controller_filters_non_main_upstream_runs_before_creation():
    text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    workflow_run = text.index("workflow_run:")
    workflow_dispatch = text.index("workflow_dispatch:")
    trigger = text[workflow_run:workflow_dispatch]

    assert 'workflows: ["wow-verify"]' in trigger
    assert "types: [completed]" in trigger
    assert "branches: [main]" in trigger


def test_non_advancing_deploy_receipts_fail_controller_after_pointer_reconcile():
    text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    reconcile = text.index("Reconcile exact Render receipt to durable deployment pointer")
    final_gate = text.index("Fail closed when no exact deployment advanced")
    assert reconcile < final_gate
    for status in (
        "NON_MAIN_NO_DEPLOY",
        "UPSTREAM_NOT_SUCCESS_NO_DEPLOY",
        "STALE_SHA_NO_DEPLOY",
        "DEPLOY_SUPERSEDED_BY_NEW_MAIN",
    ):
        assert status in text[final_gate:]
    assert '"POST_DEPLOY_CANARIES_BLOCKED"' in text[final_gate:]
    assert "raise SystemExit(1)" in text[final_gate:]


def test_exact_live_deploy_receipts_still_authorize_successful_controller():
    text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    final_gate = text.index("Fail closed when no exact deployment advanced")
    gated = text[final_gate:]

    assert "EXACT_SHA_RENDER_DEPLOY_LIVE" in gated
    assert "EXACT_SHA_ALREADY_LIVE" in gated
    assert '"EXACT_DEPLOY_ACCEPTED_FOR_CANARIES"' in gated
    assert "raise SystemExit(0)" in gated


def test_post_deploy_orchestrator_is_only_deploy_consumer_for_heavy_verification():
    orchestrator = ORCHESTRATOR_WORKFLOW.read_text(encoding="utf-8")
    cert = CERT_WORKFLOW.read_text(encoding="utf-8")
    priority = PRIORITY_WORKFLOW.read_text(encoding="utf-8")
    spread = CANARY_WORKFLOW.read_text(encoding="utf-8")

    assert 'workflows: ["wow-v17-render-production-deploy"]' in orchestrator
    assert "types: [completed]" in orchestrator
    assert "branches: [main]" in orchestrator
    assert "github.event.workflow_run.conclusion == 'success'" in orchestrator
    assert "github.event.workflow_run.head_branch == 'main'" in orchestrator

    for text in (cert, priority, spread):
        assert "workflow_call:" in text
        assert 'workflows: ["wow-v17-render-production-deploy"]' not in text


def test_post_deploy_orchestrator_fences_receipt_to_current_exact_sha():
    text = ORCHESTRATOR_WORKFLOW.read_text(encoding="utf-8")

    # Reproduction: deploy SHA A finishes after main has already advanced to B.
    # workflow_run checks may use B's workflow definition, but must not run any
    # production verification against the still-live A deployment.
    assert text.count("github.event.workflow_run.head_sha == github.sha") == 4
    for start, end in (
        ("  priority-props:", "  spread-forward:"),
        ("  spread-forward:", "  spread-certification:"),
        ("  spread-certification:", "  golden-full-slate:"),
    ):
        section = text[text.index(start):text.index(end)]
        assert "github.event.workflow_run.head_sha == github.sha" in section
    golden = text[text.index("  golden-full-slate:"):]
    assert "github.event.workflow_run.head_sha == github.sha" in golden


def test_post_deploy_orchestrator_runs_bounded_smoke_before_memory_heavy_replay():
    text = ORCHESTRATOR_WORKFLOW.read_text(encoding="utf-8")

    priority = text.index("  priority-props:")
    spread = text.index("  spread-forward:")
    cert = text.index("  spread-certification:")
    assert priority < spread < cert
    assert "needs: priority-props" in text
    assert "needs: spread-forward" in text
    assert text.count("always() &&") == 3
    assert "uses: ./.github/workflows/wow-v17-priority-prop-lifecycle.yml" in text
    assert "post_deploy_smoke: true" in text
    assert "uses: ./.github/workflows/wow-v17-spread-forward-production-canary.yml" in text
    assert "uses: ./.github/workflows/wow-v17-spread-certification-replay.yml" in text



def test_golden_full_slate_runs_only_as_post_deploy_reusable_acceptance():
    orchestrator = ORCHESTRATOR_WORKFLOW.read_text(encoding="utf-8")
    daily = DAILY_WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_call:" in daily
    assert "post_deploy_acceptance:" in daily
    assert "\n  push:\n" not in daily
    assert "if: inputs.post_deploy_acceptance == true" in daily

    golden = orchestrator.index("  golden-full-slate:")
    certification = orchestrator.index("  spread-certification:")
    assert certification < golden
    section = orchestrator[golden:]
    assert "needs: spread-certification" in section
    assert "uses: ./.github/workflows/wow-v17-daily-snapshot.yml" in section
    assert "post_deploy_acceptance: true" in section
    assert "github.event_name == 'workflow_run'" in section
    assert "github.event.workflow_run.conclusion == 'success'" in section
    assert "github.event.workflow_run.head_branch == 'main'" in section
    assert "github.event_name == 'workflow_dispatch'" not in section


def test_priority_prop_deploy_smoke_skips_next_day_without_weakening_hourly_default():
    text = PRIORITY_WORKFLOW.read_text(encoding="utf-8")

    assert "post_deploy_smoke:" in text
    assert "type: boolean" in text
    assert "default: false" in text
    assert "max-parallel: 1" in text
    assert 'POST_DEPLOY_SMOKE: ${{ inputs.post_deploy_smoke && \'true\' || \'false\' }}' in text
    assert 'if [ "${POST_DEPLOY_SMOKE}" != "true" ]; then' in text
    assert 'seed_date "${tomorrow}"' in text
    assert 'seed_date "${today}"' in text


def test_spread_canary_serializes_runtime_heavy_jobs_without_skipping_after_failure():
    text = CANARY_WORKFLOW.read_text(encoding="utf-8")

    assert "needs: ncaaf-canary" in text
    assert "needs: nfl-canary" in text
    assert "needs: wnba-canary" in text
    assert text.count("if: always()") == 3

    ncaaf = text.index("  ncaaf-canary:")
    nfl = text.index("  nfl-canary:")
    wnba = text.index("  wnba-canary:")
    mlb = text.index("  mlb-canary:")
    assert ncaaf < nfl < wnba < mlb


def test_wnba_canary_preserves_exact_governed_source_blocker():
    text = CANARY_WORKFLOW.read_text(encoding="utf-8")
    wnba = text[text.index("  wnba-canary:"):text.index("  mlb-canary:")]

    assert 'if result.get("status")=="BLOCKED":' in wnba
    assert 'WNBA_SPREAD_CANARY_IDENTITY_SOURCE_UNAVAILABLE' in wnba
    assert '"status":"BLOCKED_WITH_EXACT_REASON"' in wnba
    assert 'raise RuntimeError(f"BLOCKED_WITH_EXACT_REASON:' in wnba
    assert 'assert detail, result' in wnba
    assert 'elapsed_seconds' in wnba


def test_release_gate_preserves_non_execution_governance():
    text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert 'WOW_CAN_EXECUTE: "false"' in text
    assert 'WOW_DRY_RUN_ONLY: "true"' in text
    assert '"can_execute":False' in text or '"can_execute": False' in text
