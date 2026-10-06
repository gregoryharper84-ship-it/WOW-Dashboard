from fastapi import FastAPI
from fastapi.testclient import TestClient

from v17.reliability_http import install_reliability_headers


def test_reliability_headers_are_present_and_idempotent():
    app = FastAPI()
    assert install_reliability_headers(app) is True
    assert install_reliability_headers(app) is True

    @app.get("/health/live")
    def health():
        return {"status": "ok", "can_execute": False}

    response = TestClient(app).get("/health/live")
    assert response.status_code == 200
    assert response.headers["X-WOW-Dry-Run-Only"] == "true"
    assert response.headers["X-WOW-Can-Execute"] == "false"
    assert response.json()["can_execute"] is False
