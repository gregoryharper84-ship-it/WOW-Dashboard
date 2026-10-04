from pathlib import Path
from types import SimpleNamespace

import yaml
from fastapi import FastAPI

from v17 import scout_handoff_queue_installer as installer


ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "artifacts" / "wow-engine" / "api_ncaaf_acceptance.py"
WORKFLOW = ROOT / ".github" / "workflows" / "wow-v17-nightly-multiscout.yml"
RENDER = ROOT / "render.yaml"


def test_durable_handoff_rebuilds_server_plan_from_raw_scout_evidence(monkeypatch):
    app = FastAPI()

    @app.post("/score-pick-request")
    def score_pick_request(payload: dict):
        return payload

    @app.post("/score-team-event-request")
    def score_team_event_request(payload: dict):
        return payload

    raw = {
        "run_id": "wow-scout-activation-test",
        "research_run_id": "wow-scout-activation-test",
        "status": "DISCOVERY_COMPLETE",
        "model_handoff_ready": True,
        "governance": {"can_execute": False},
        "model_handoff": {"prop_candidates": [], "team_event_candidates": []},
        "can_execute": False,
    }
    plan = SimpleNamespace(can_execute=False)
    seen = {}

    def fake_build_handoff_plan(payload):
        seen["raw"] = payload
        return plan

    def fake_enqueue_plan(db, rebuilt):
        seen["db"] = db
        seen["plan"] = rebuilt
        return {
            "schema_version": "wow.v17.scout-handoff-enqueue.v1",
            "source_run_id": raw["run_id"],
            "research_run_id": raw["research_run_id"],
            "can_execute": False,
        }

    monkeypatch.setattr(installer.queue, "build_handoff_plan", fake_build_handoff_plan)
    monkeypatch.setattr(installer.queue, "enqueue_plan", fake_enqueue_plan)

    installed, prop_score_fn, team_score_fn = installer.install_scout_handoff_routes(
        app,
        db_client_fn=lambda: "db-client",
    )

    assert installed is True
    assert callable(prop_score_fn)
    assert callable(team_score_fn)
    submit = next(
        route.endpoint
        for route in app.router.routes
        if getattr(route, "path", None) == "/v17/scout-handoff-runs"
        and "POST" in (getattr(route, "methods", set()) or set())
    )
    receipt = submit(raw)

    assert seen == {"raw": raw, "db": "db-client", "plan": plan}
    assert receipt["can_execute"] is False


def test_production_entrypoint_schedules_feature_gated_handoff_installer():
    text = API.read_text(encoding="utf-8")
    assert "from v17.scout_handoff_queue_installer import schedule_scout_handoff_queue" in text
    assert "schedule_scout_handoff_queue(app, db_client_fn=_db_client)" in text


def test_render_rollout_enables_one_bounded_handoff_worker():
    render = yaml.safe_load(RENDER.read_text(encoding="utf-8"))
    service = next(
        item for item in render["services"]
        if item.get("name") == "wow-governed-probability-engine"
    )
    env = {item["key"]: item for item in service["envVars"]}
    assert env["WOW_SCOUT_ASYNC_HANDOFF_ENABLED"]["value"] == "true"
    assert env["WOW_SCOUT_HANDOFF_WORKERS"]["value"] == "1"
    assert env["WOW_CAN_EXECUTE"]["value"] == "false"
    assert env["WOW_DRY_RUN_ONLY"]["value"] == "true"


def test_nightly_caller_uses_same_async_handoff_feature_gate():
    text = WORKFLOW.read_text(encoding="utf-8")
    live = text.split("  nightly-discovery:\n", 1)[1]
    assert 'WOW_SCOUT_ASYNC_HANDOFF_ENABLED: "true"' in live
    assert 'WOW_CAN_EXECUTE: "false"' in live
    assert 'WOW_DRY_RUN_ONLY: "true"' in live


def test_nightly_acceptance_waits_for_terminal_queue_reconciliation():
    text = WORKFLOW.read_text(encoding="utf-8")
    live = text.split("  nightly-discovery:\n", 1)[1]
    assert "Require terminal durable handoff reconciliation" in live
    assert "python -m v17.scout_handoff_terminal_acceptance" in live
    assert "python v17/scout_handoff_terminal_acceptance.py" not in live
    assert "terminal-acceptance-receipt.json" in live
    assert '"COMPLETE"' in live
    assert '"IN_PROGRESS"' in live


def test_live_multiscout_waits_for_successful_render_deployment():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert 'workflow_run:' in text
    assert 'workflows: ["wow-v17-render-production-deploy"]' in text
    assert "github.event.workflow_run.conclusion == 'success'" in text
    assert "github.event.workflow_run.head_branch == 'main'" in text
    assert "github.event_name == 'schedule'" in text
    assert "github.event_name == 'workflow_dispatch'" in text

    # Push/PR still run verification, but production discovery is deploy-triggered.
    live = text.split("  nightly-discovery:\n", 1)[1]
    live_header = live.split("    runs-on:", 1)[0]
    assert "github.event_name == 'push'" not in live_header
    assert "github.event_name != 'pull_request'" not in live_header


def test_deploy_triggered_jobs_checkout_exact_deployed_sha():
    text = WORKFLOW.read_text(encoding="utf-8")
    exact_ref = "github.event.workflow_run.head_sha"
    assert text.count(exact_ref) >= 3
    assert "github.event_name == 'workflow_run' && github.event.workflow_run.head_sha || github.sha" in text
