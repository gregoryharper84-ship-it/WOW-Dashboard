from fastapi import FastAPI, Header, Query

import pick_request_runtime_core as pick_runtime
from v17 import pick_request_run_control as control
from v17 import pick_request_durable_job_queue_installer as installer


def test_durable_queue_installer_replaces_routes_without_mutating_base_functions():
    app = FastAPI()

    @app.post("/score-pick-request", operation_id="scoreWowPickRequest")
    def score(
        batch: pick_runtime.PickRequestBatch,
        x_wow_model_identity: str | None = Header(default=None, alias="X-WOW-Model-Identity"),
    ):
        return {"request_id": batch.request_id, "reconciliation_pass": True, "can_execute": False}

    @app.get("/v17/pick-request-runs/{request_id}", operation_id="getWowV17PickRequestRunState")
    def read(
        request_id: str,
        offset: int = Query(default=0),
        limit: int = Query(default=100),
        include_outcomes: bool = Query(default=False),
    ):
        return {"request_id": request_id, "can_execute": False}

    @app.post("/v17/pick-request-runs/resumable", operation_id="runWowV17ResumablePickRequest")
    def run(
        request: control.ResumablePickRunRequest,
        x_wow_model_identity: str | None = Header(default=None, alias="X-WOW-Model-Identity"),
    ):
        return {"request_id": request.request_id, "can_execute": False}

    @app.post("/v17/pick-request-runs/{request_id}/close", operation_id="closeWowV17PickRequestRun")
    def close(request_id: str, request: control.ClosePickRunRequest):
        return {"request_id": request_id, "can_execute": False}

    base_run = control.run_resumable
    base_read = control.read_run_state
    base_close = control.close_run

    installed, score_fn = installer.install_durable_pick_job_queue_routes(
        app,
        db_client_fn=lambda: object(),
    )

    assert installed is True
    assert callable(score_fn)
    assert control.run_resumable is base_run
    assert control.read_run_state is base_read
    assert control.close_run is base_close

    operation_ids = {
        getattr(route, "operation_id", None)
        for route in app.router.routes
    }
    assert "getWowV17PickRequestRunState" in operation_ids
    assert "runWowV17ResumablePickRequest" in operation_ids
    assert "closeWowV17PickRequestRun" in operation_ids
