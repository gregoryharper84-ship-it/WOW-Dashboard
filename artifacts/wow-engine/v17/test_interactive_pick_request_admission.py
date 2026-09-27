import threading
import time
from types import SimpleNamespace

from fastapi import FastAPI

from v17.interactive_pick_parallel import install_interactive_pick_parallel_wrapper


def _score_route(app: FastAPI):
    return next(
        route
        for route in app.router.routes
        if getattr(route, "path", None) == "/score-pick-request"
        and "POST" in (getattr(route, "methods", set()) or set())
    )


def test_request_admission_serializes_overlapping_score_calls(monkeypatch) -> None:
    monkeypatch.setenv("WOW_INTERACTIVE_PROP_REQUEST_WORKERS", "1")
    monkeypatch.setenv("WOW_INTERACTIVE_PROP_SCORE_WORKERS", "1")

    app = FastAPI()
    active = 0
    max_active = 0
    counter_lock = threading.Lock()
    start_barrier = threading.Barrier(3)

    @app.post("/score-pick-request")
    def canonical_score(batch):
        nonlocal active, max_active
        with counter_lock:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.08)
            return {"request_id": batch.request_id, "can_execute": False}
        finally:
            with counter_lock:
                active -= 1

    assert install_interactive_pick_parallel_wrapper(app, market_api=None) is True
    endpoint = _score_route(app).endpoint

    errors: list[BaseException] = []

    def invoke(request_id: str) -> None:
        try:
            start_barrier.wait(timeout=2)
            batch = SimpleNamespace(
                request_id=request_id,
                response_mode="FULL",
                rows=[object()],
            )
            response = endpoint(batch, None)
            assert response["can_execute"] is False
        except BaseException as exc:  # pragma: no cover - only collected for assertion below
            errors.append(exc)

    first = threading.Thread(target=invoke, args=("admission-a",))
    second = threading.Thread(target=invoke, args=("admission-b",))
    first.start()
    second.start()
    start_barrier.wait(timeout=2)
    first.join(timeout=2)
    second.join(timeout=2)

    assert not errors
    assert not first.is_alive()
    assert not second.is_alive()
    assert max_active == 1
