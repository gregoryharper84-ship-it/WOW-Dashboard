from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"PATCH_EXPECTATION_FAILED:{path}:{count}:{old[:80]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_exact_count(path: Path, old: str, new: str, expected: int) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != expected:
        raise SystemExit(f"PATCH_EXPECTATION_FAILED:{path}:{count}!={expected}:{old[:80]!r}")
    path.write_text(text.replace(old, new), encoding="utf-8")


cert = ROOT / ".github/workflows/wow-v17-spread-certification-replay.yml"
priority = ROOT / ".github/workflows/wow-v17-priority-prop-lifecycle.yml"
spread = ROOT / ".github/workflows/wow-v17-spread-forward-production-canary.yml"
post_test = ROOT / "artifacts/wow-engine/tests/test_v17_post_deploy_canary_gate.py"
priority_test = ROOT / "artifacts/wow-engine/v17/test_priority_prop_lifecycle_workflow.py"
orchestrator = ROOT / ".github/workflows/wow-v17-post-deploy-verification-orchestrator.yml"

replace_once(
    cert,
    '''on:\n  workflow_run:\n    workflows: ["wow-v17-render-production-deploy"]\n    types: [completed]\n''',
    '''on:\n  workflow_call:\n  workflow_dispatch:\n''',
)
replace_once(
    cert,
    '''    if: >-\n      github.event.workflow_run.conclusion == 'success' &&\n      github.event.workflow_run.head_branch == 'main'\n''',
    '',
)

replace_once(
    priority,
    '''on:\n  schedule:\n    - cron: "7 * * * *"\n  workflow_dispatch:\n  workflow_run:\n    workflows: ["wow-v17-render-production-deploy"]\n    types: [completed]\n''',
    '''on:\n  schedule:\n    - cron: "7 * * * *"\n  workflow_dispatch:\n  workflow_call:\n''',
)
replace_once(
    priority,
    '''    if: >-\n      github.event_name != 'workflow_run' ||\n      (github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_branch == 'main')\n''',
    '',
)

replace_once(
    spread,
    '''on:\n  workflow_run:\n    workflows: ["wow-v17-render-production-deploy"]\n    types: [completed]\n  workflow_dispatch:\n''',
    '''on:\n  workflow_call:\n  workflow_dispatch:\n''',
)
replace_once(
    spread,
    '''    if: >-\n      github.event_name == 'workflow_dispatch' ||\n      (github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_branch == 'main')\n''',
    '',
)
replace_exact_count(
    spread,
    '''    if: >-\n      always() &&\n      (github.event_name == 'workflow_dispatch' ||\n       (github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_branch == 'main'))\n''',
    '''    if: always()\n''',
    3,
)

orchestrator.write_text(
    '''name: wow-v17-post-deploy-verification-orchestrator\n\non:\n  workflow_run:\n    workflows: ["wow-v17-render-production-deploy"]\n    types: [completed]\n    branches: [main]\n  workflow_dispatch:\n\npermissions:\n  contents: read\n  id-token: write\n\nconcurrency:\n  group: wow-v17-post-deploy-verification-orchestrator\n  cancel-in-progress: false\n\njobs:\n  spread-certification:\n    name: Run spread certification replay\n    if: >-\n      github.event_name == 'workflow_dispatch' ||\n      (github.event.workflow_run.conclusion == 'success' &&\n       github.event.workflow_run.head_branch == 'main')\n    uses: ./.github/workflows/wow-v17-spread-certification-replay.yml\n    secrets: inherit\n\n  priority-props:\n    name: Run priority prop lifecycle\n    needs: spread-certification\n    if: >-\n      always() &&\n      (github.event_name == 'workflow_dispatch' ||\n       (github.event.workflow_run.conclusion == 'success' &&\n        github.event.workflow_run.head_branch == 'main'))\n    uses: ./.github/workflows/wow-v17-priority-prop-lifecycle.yml\n    secrets: inherit\n\n  spread-forward:\n    name: Run spread-forward production canaries\n    needs: priority-props\n    if: >-\n      always() &&\n      (github.event_name == 'workflow_dispatch' ||\n       (github.event.workflow_run.conclusion == 'success' &&\n        github.event.workflow_run.head_branch == 'main'))\n    uses: ./.github/workflows/wow-v17-spread-forward-production-canary.yml\n    secrets: inherit\n''',
    encoding="utf-8",
)

post_test.write_text(
    '''from pathlib import Path\n\n\nREPO_ROOT = Path(__file__).resolve().parents[3]\nDEPLOY_WORKFLOW = REPO_ROOT / ".github/workflows/wow-v17-render-production-deploy.yml"\nORCHESTRATOR_WORKFLOW = REPO_ROOT / ".github/workflows/wow-v17-post-deploy-verification-orchestrator.yml"\nCANARY_WORKFLOW = REPO_ROOT / ".github/workflows/wow-v17-spread-forward-production-canary.yml"\nCERT_WORKFLOW = REPO_ROOT / ".github/workflows/wow-v17-spread-certification-replay.yml"\nPRIORITY_WORKFLOW = REPO_ROOT / ".github/workflows/wow-v17-priority-prop-lifecycle.yml"\n\n\ndef test_deploy_controller_filters_non_main_upstream_runs_before_creation():\n    text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")\n    workflow_run = text.index("workflow_run:")\n    workflow_dispatch = text.index("workflow_dispatch:")\n    trigger = text[workflow_run:workflow_dispatch]\n\n    assert 'workflows: ["wow-verify"]' in trigger\n    assert "types: [completed]" in trigger\n    assert "branches: [main]" in trigger\n\n\ndef test_non_advancing_deploy_receipts_fail_controller_after_pointer_reconcile():\n    text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")\n\n    reconcile = text.index("Reconcile exact Render receipt to durable deployment pointer")\n    final_gate = text.index("Fail closed when no exact deployment advanced")\n    assert reconcile < final_gate\n    for status in (\n        "NON_MAIN_NO_DEPLOY",\n        "UPSTREAM_NOT_SUCCESS_NO_DEPLOY",\n        "STALE_SHA_NO_DEPLOY",\n        "DEPLOY_SUPERSEDED_BY_NEW_MAIN",\n    ):\n        assert status in text[final_gate:]\n    assert '\"POST_DEPLOY_CANARIES_BLOCKED\"' in text[final_gate:]\n    assert "raise SystemExit(1)" in text[final_gate:]\n\n\ndef test_exact_live_deploy_receipts_still_authorize_successful_controller():\n    text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")\n    final_gate = text.index("Fail closed when no exact deployment advanced")\n    gated = text[final_gate:]\n\n    assert "EXACT_SHA_RENDER_DEPLOY_LIVE" in gated\n    assert "EXACT_SHA_ALREADY_LIVE" in gated\n    assert '\"EXACT_DEPLOY_ACCEPTED_FOR_CANARIES\"' in gated\n    assert "raise SystemExit(0)" in gated\n\n\ndef test_post_deploy_orchestrator_is_only_deploy_consumer_for_heavy_verification():\n    orchestrator = ORCHESTRATOR_WORKFLOW.read_text(encoding="utf-8")\n    cert = CERT_WORKFLOW.read_text(encoding="utf-8")\n    priority = PRIORITY_WORKFLOW.read_text(encoding="utf-8")\n    spread = CANARY_WORKFLOW.read_text(encoding="utf-8")\n\n    assert 'workflows: ["wow-v17-render-production-deploy"]' in orchestrator\n    assert "types: [completed]" in orchestrator\n    assert "branches: [main]" in orchestrator\n    assert "github.event.workflow_run.conclusion == 'success'" in orchestrator\n    assert "github.event.workflow_run.head_branch == 'main'" in orchestrator\n\n    for text in (cert, priority, spread):\n        assert "workflow_call:" in text\n        assert 'workflows: ["wow-v17-render-production-deploy"]' not in text\n\n\ndef test_post_deploy_orchestrator_sequences_all_heavy_verification_without_skip():\n    text = ORCHESTRATOR_WORKFLOW.read_text(encoding="utf-8")\n\n    cert = text.index("  spread-certification:")\n    priority = text.index("  priority-props:")\n    spread = text.index("  spread-forward:")\n    assert cert < priority < spread\n    assert "needs: spread-certification" in text\n    assert "needs: priority-props" in text\n    assert text.count("always() &&") == 2\n    assert "uses: ./.github/workflows/wow-v17-spread-certification-replay.yml" in text\n    assert "uses: ./.github/workflows/wow-v17-priority-prop-lifecycle.yml" in text\n    assert "uses: ./.github/workflows/wow-v17-spread-forward-production-canary.yml" in text\n\n\ndef test_spread_canary_serializes_runtime_heavy_jobs_without_skipping_after_failure():\n    text = CANARY_WORKFLOW.read_text(encoding="utf-8")\n\n    assert "needs: ncaaf-canary" in text\n    assert "needs: nfl-canary" in text\n    assert "needs: wnba-canary" in text\n    assert text.count("if: always()") == 3\n\n    ncaaf = text.index("  ncaaf-canary:")\n    nfl = text.index("  nfl-canary:")\n    wnba = text.index("  wnba-canary:")\n    mlb = text.index("  mlb-canary:")\n    assert ncaaf < nfl < wnba < mlb\n\n\ndef test_wnba_canary_preserves_exact_governed_source_blocker():\n    text = CANARY_WORKFLOW.read_text(encoding="utf-8")\n    wnba = text[text.index("  wnba-canary:"):text.index("  mlb-canary:")]\n\n    assert 'if result.get("status")=="BLOCKED":' in wnba\n    assert 'WNBA_SPREAD_CANARY_IDENTITY_SOURCE_UNAVAILABLE' in wnba\n    assert '\"status\":\"BLOCKED_WITH_EXACT_REASON\"' in wnba\n    assert 'raise RuntimeError(f"BLOCKED_WITH_EXACT_REASON:' in wnba\n    assert 'assert detail, result' in wnba\n    assert 'elapsed_seconds' in wnba\n\n\ndef test_release_gate_preserves_non_execution_governance():\n    text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")\n\n    assert 'WOW_CAN_EXECUTE: "false"' in text\n    assert 'WOW_DRY_RUN_ONLY: "true"' in text\n    assert '\"can_execute\":False' in text or '\"can_execute\": False' in text\n''',
    encoding="utf-8",
)

priority_text = priority_test.read_text(encoding="utf-8")
priority_text = priority_text.replace(
    '''    assert 'workflow_run:' in text\n    assert 'workflows: ["wow-v17-render-production-deploy"]' in text\n    assert 'types: [completed]' in text\n    assert '\\n  push:\\n' not in text\n    assert "github.event.workflow_run.conclusion == 'success'" in text\n    assert "github.event.workflow_run.head_branch == 'main'" in text\n''',
    '''    assert 'workflow_call:' in text\n    assert 'workflows: ["wow-v17-render-production-deploy"]' not in text\n    assert 'workflow_run:' not in text\n    assert '\\n  push:\\n' not in text\n''',
    1,
)
if "assert 'workflow_call:' in text" not in priority_text:
    raise SystemExit("PATCH_EXPECTATION_FAILED:priority test contract")
priority_test.write_text(priority_text, encoding="utf-8")

print("PATCHED_POST_DEPLOY_ORCHESTRATION_1087")
