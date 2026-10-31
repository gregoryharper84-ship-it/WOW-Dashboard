"""Phase 1 integration/contract tests, hitting the real HTTP routes on the
actual production app composition (api_prod.app), with a fake Supabase
client injected — same pattern as the existing test_api_prod.py suite.

Since the convergence pass with PR #33 (feature/wow-agent-runtime-v1),
POST /wow/runs starts real orchestration, and conftest.py runs Celery
eagerly (in-process, no real broker) for this whole test suite — so a run
created through the API can already be terminal by the time the response
comes back, unlike a real async deployment. Tests that need to observe or
act on a *nonterminal* run construct it directly via
agent_runtime.repository.create_run(), bypassing the API's orchestration
side effect, rather than through POST /wow/runs.

test_synchronous_fake_worker_fixture_completes_with_balanced_reconciliation
is Phase 1's exit criterion from the packet (section 22): a synchronous
fake-worker fixture completes with balanced reconciliation, proven through
the real run ledger, state machine, idempotency, reducer, and reconciliation
code — surfaced through the real polling endpoint, not just unit-tested in
isolation. It builds the run directly through the repository for the same
reason as above: this test manually drives fake jobs job-by-job and needs
the run to stay exactly where it puts it, not race ahead through real
orchestration.
"""
from __future__ import annotations

import os
import secrets

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import agent_runtime_api
import api_prod
from agent_runtime import idempotency, repository, schemas
from agent_runtime.reconciliation import reconcile_from_ceilings
from agent_runtime.reducer import RequiredJobResult, reduce_candidate
from agent_runtime.registry import WORKERS
from agent_runtime_test_support import FakeSupabaseClient

client = TestClient(api_prod.app)

@pytest.fixture(autouse=True)
def _agent_runtime_auth_config(monkeypatch):
    # Independent per-test credentials; never use a shared static bearer.
    scoring_key = secrets.token_urlsafe(32)
    runtime_key = secrets.token_urlsafe(32)
    monkeypatch.setenv("WOW_ACTION_API_KEY", scoring_key)
    monkeypatch.setenv("WOW_AGENT_RUNTIME_API_KEY", runtime_key)
    monkeypatch.setenv("REDIS_URL", "redis://fake-for-test/0")

    class FakeAdmissionRedis:
        def __init__(self):
            self.now_ms = 0
            self.admissions = {}

        def eval(self, script, numkeys, key, limit, window_ms, member):
            assert numkeys == 1
            assert "ZREMRANGEBYSCORE" in script and "TIME" in script
            self.admissions = {
                k: t for k, t in self.admissions.items()
                if t > self.now_ms - int(window_ms)
            }
            if member in self.admissions:
                return 2
            if len(self.admissions) >= int(limit):
                return 0
            self.admissions[member] = self.now_ms
            return 1

    fake_rate = FakeAdmissionRedis()
    agent_runtime_api._admission_redis.cache_clear()
    monkeypatch.setattr("redis.Redis.from_url", lambda *args, **kwargs: fake_rate)
    return {"Authorization": f"Bearer {runtime_key}"}


@pytest.fixture
def auth_headers(_agent_runtime_auth_config):
    return _agent_runtime_auth_config

_RUN_PAYLOAD = {
    "run_type": "FULL_MODEL",
    "as_of": "2026-08-29T00:00:00Z",
    "user_timezone": "America/Chicago",
    "lanes": ["PLAYER_PROPS"],
    "sports": ["MLB"],
    "discovery_enabled": False,
    "candidate_inputs": [],
}


def _seed_registry(fake: FakeSupabaseClient) -> None:
    for spec in WORKERS.values():
        fake.table("wow_agent_worker_registry").insert({
            "worker_id": spec.worker_id, "worker_version": spec.worker_version,
            "contract_version": spec.contract_version, "implementation_type": spec.implementation_type,
            "authority_ceiling": spec.authority_ceiling, "enabled": True,
        }).execute()


def _created_run(fake: FakeSupabaseClient, idempotency_key: str = "k1") -> dict:
    """Build a run directly through the repository, bypassing POST
    /wow/runs's orchestration side effect, so it stays exactly at CREATED
    for tests that need a controllable nonterminal fixture."""
    row, _ = repository.create_run(
        fake, idempotency_key=idempotency_key, request_hash="h1", run_type="FULL_MODEL",
        requested_as_of="2026-08-29T00:00:00Z", user_timezone="America/Chicago",
        governance_version="TEST_V1",
    )
    return row


def test_health_live_has_no_dependencies():
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "can_execute": False}


def test_health_ready_all_dependencies_ok(monkeypatch):
    fake = FakeSupabaseClient()
    _seed_registry(fake)
    monkeypatch.setattr(agent_runtime_api, "get_client", lambda: fake)
    monkeypatch.setenv("REDIS_URL", "redis://fake-for-test/0")

    class _FakePing:
        def ping(self):
            return True

    monkeypatch.setattr("redis.Redis.from_url", lambda *a, **k: _FakePing())

    response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "database": "ok", "queue": "ok", "worker_registry": "ok", "can_execute": False}


def test_health_ready_fails_closed_when_database_unreachable(monkeypatch):
    def _broken_client():
        raise RuntimeError("no database configured")

    monkeypatch.setattr(agent_runtime_api, "get_client", _broken_client)
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["detail"]["database"] == "unreachable"


def test_health_ready_fails_closed_when_registry_mismatched(monkeypatch):
    fake = FakeSupabaseClient()
    monkeypatch.setattr(agent_runtime_api, "get_client", lambda: fake)
    monkeypatch.setenv("REDIS_URL", "redis://fake-for-test/0")
    monkeypatch.setattr("redis.Redis.from_url", lambda *a, **k: type("P", (), {"ping": lambda self: True})())

    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["detail"]["worker_registry"] == "mismatch_or_unreachable"


def test_run_and_cancel_require_existing_bearer_key(monkeypatch):
    fake = FakeSupabaseClient()
    run = _created_run(fake, "cancel-auth-check")

    def forbidden_db_access():
        raise AssertionError("unauthenticated request accessed service-role database")

    monkeypatch.setattr(agent_runtime_api, "get_client", forbidden_db_access)

    anonymous = client.post(
        "/wow/runs", json=_RUN_PAYLOAD,
        headers={"Idempotency-Key": "anonymous-rejected"},
    )
    assert anonymous.status_code == 401
    invalid = client.post(
        "/wow/runs", json=_RUN_PAYLOAD,
        headers={"Idempotency-Key": "invalid-rejected", "Authorization": "Bearer wrong"},
    )
    assert invalid.status_code == 401
    assert len(fake._store.get("wow_agent_runs", [])) == 1
    assert len(fake._store.get("wow_agent_jobs", [])) == 0

    unauthorized_cancel = client.post(f"/wow/runs/{run['run_id']}/cancel")
    assert unauthorized_cancel.status_code == 401
    assert repository.get_run(fake, run["run_id"])["status"] == "CREATED"


def test_run_reads_require_existing_bearer_before_database_access(monkeypatch):
    def forbidden_db_access():
        raise AssertionError("unauthorized GET accessed service-role database")

    monkeypatch.setattr(agent_runtime_api, "get_client", forbidden_db_access)
    run_id = "00000000-0000-0000-0000-000000000000"
    for path in (f"/wow/runs/{run_id}", f"/wow/runs/{run_id}/manifest", f"/wow/runs/{run_id}/audit"):
        for headers in ({}, {"Authorization": "Bearer invalid"}):
            response = client.get(path, headers=headers)
            assert response.status_code == 401, (path, response.text)


def test_authenticated_run_reads_preserve_contract(monkeypatch, auth_headers):
    fake = FakeSupabaseClient()
    created = _created_run(fake, "read-auth-contract")
    monkeypatch.setattr(agent_runtime_api, "get_client", lambda: fake)
    run_id = created["run_id"]
    state = client.get(f"/wow/runs/{run_id}", headers=auth_headers)
    assert state.status_code == 200
    assert state.json()["run_id"] == run_id
    assert state.json()["can_execute"] is False
    manifest = client.get(f"/wow/runs/{run_id}/manifest", headers=auth_headers)
    assert manifest.status_code == 200
    assert manifest.json()["terminal"] is False
    audit = client.get(f"/wow/runs/{run_id}/audit", headers=auth_headers)
    assert audit.status_code == 200
    assert audit.json()["run_id"] == run_id
    assert audit.json()["can_execute"] is False


def test_malformed_candidate_batch_fails_closed_before_database_access(monkeypatch, auth_headers):
    """HTTP schema and internal direct-call paths both reject bad batches."""
    def forbidden_db_access():
        raise AssertionError("malformed admission accessed service-role database")

    monkeypatch.setattr(agent_runtime_api, "get_client", forbidden_db_access)
    for invalid in (None, 12, {}, "not-a-list", [12]):
        response = client.post(
            "/wow/runs",
            json={**_RUN_PAYLOAD, "candidate_inputs": invalid},
            headers={"Idempotency-Key": "invalid-batch", **auth_headers},
        )
        assert response.status_code == 422, (invalid, response.text)

    # An internal caller using model_construct can bypass Pydantic validation;
    # the route must still return a typed 422, never a TypeError or DB access.
    req = schemas.RunCreateRequest.model_construct(
        **{**_RUN_PAYLOAD, "candidate_inputs": None},
    )
    with pytest.raises(HTTPException) as exc:
        agent_runtime_api.create_run(req, idempotency_key="direct-invalid-batch")
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "RUN_BATCH_LIMIT_EXCEEDED"


def test_run_batch_and_key_limits_reject_before_database_access(monkeypatch, auth_headers):
    def forbidden_db_access():
        raise AssertionError("oversized admission accessed service-role database")

    monkeypatch.setattr(agent_runtime_api, "get_client", forbidden_db_access)
    batch = client.post(
        "/wow/runs", json={**_RUN_PAYLOAD, "candidate_inputs": [{}] * 33},
        headers={"Idempotency-Key": "batch-limit", **auth_headers},
    )
    assert batch.status_code == 422
    assert batch.json()["detail"]["code"] == "RUN_BATCH_LIMIT_EXCEEDED"
    key = client.post(
        "/wow/runs", json=_RUN_PAYLOAD,
        headers={"Idempotency-Key": "x" * 129, **auth_headers},
    )
    assert key.status_code == 422
    assert key.json()["detail"]["code"] == "IDEMPOTENCY_KEY_TOO_LONG"

    unsafe_key = client.post(
        "/wow/runs", json=_RUN_PAYLOAD,
        headers={"Idempotency-Key": "key with spaces", **auth_headers},
    )
    assert unsafe_key.status_code == 422
    assert unsafe_key.json()["detail"]["code"] == "IDEMPOTENCY_KEY_INVALID_CHARACTERS"

    large_candidate = client.post(
        "/wow/runs",
        json={**_RUN_PAYLOAD, "candidate_inputs": [{"payload": "x" * 65536}]},
        headers={"Idempotency-Key": "bounded-payload", **auth_headers},
    )
    assert large_candidate.status_code == 422
    assert large_candidate.json()["detail"]["code"] == "RUN_CANDIDATE_PAYLOAD_TOO_LARGE"

    # An internal caller can bypass HTTP header validation. The same
    # pre-DB guard must reject unsafe idempotency keys without any writes.
    direct = schemas.RunCreateRequest.model_construct(**_RUN_PAYLOAD)
    with pytest.raises(HTTPException) as invalid_key:
        agent_runtime_api.create_run(direct, idempotency_key="control\\ncharacter")
    assert invalid_key.value.status_code == 422
    assert invalid_key.value.detail["code"] == "IDEMPOTENCY_KEY_INVALID_CHARACTERS"



def test_scoped_runtime_key_rejects_scoring_key_and_unconfigured_key(monkeypatch, auth_headers):
    def no_db():
        raise AssertionError("bad scoped auth accessed DB")
    monkeypatch.setattr(agent_runtime_api, "get_client", no_db)
    scoring_key = __import__("os").environ["WOW_ACTION_API_KEY"]
    response = client.post("/wow/runs", json=_RUN_PAYLOAD,
        headers={"Authorization": f"Bearer {scoring_key}", "Idempotency-Key": "wrong-scope"})
    assert response.status_code == 401
    monkeypatch.delenv("WOW_AGENT_RUNTIME_API_KEY")
    response = client.post("/wow/runs", json=_RUN_PAYLOAD,
        headers={**auth_headers, "Idempotency-Key": "unset-runtime"})
    assert response.status_code == 401
    monkeypatch.setenv("WOW_AGENT_RUNTIME_API_KEY", scoring_key)
    response = client.post("/wow/runs", json=_RUN_PAYLOAD,
        headers={"Authorization": f"Bearer {scoring_key}", "Idempotency-Key": "same-key"})
    assert response.status_code == 401


def test_runtime_non_ascii_bearer_and_config_fail_closed(monkeypatch, auth_headers):
    # The direct FastAPI dependency must not leak a compare_digest TypeError
    # as HTTP 500 for a raw non-ASCII Authorization header.
    with pytest.raises(HTTPException) as bad_bearer:
        agent_runtime_api._require_agent_runtime_api_key("Bearer bad-\u00e9")
    assert bad_bearer.value.status_code == 401
    monkeypatch.setenv("WOW_AGENT_RUNTIME_API_KEY", "\u00e9" * 40)
    with pytest.raises(HTTPException) as bad_config:
        agent_runtime_api._require_agent_runtime_api_key(auth_headers["Authorization"])
    assert bad_config.value.status_code == 401
    # A non-ASCII scoring key cannot crash the independent runtime-key check.
    monkeypatch.setenv("WOW_AGENT_RUNTIME_API_KEY", auth_headers["Authorization"].split(" ", 1)[1])
    monkeypatch.setenv("WOW_ACTION_API_KEY", "\u00e9" * 40)
    agent_runtime_api._require_agent_runtime_api_key(auth_headers["Authorization"])


def test_run_id_validation_rejects_before_db(monkeypatch, auth_headers):
    def no_db():
        raise AssertionError("invalid UUID reached DB")
    monkeypatch.setattr(agent_runtime_api, "get_client", no_db)
    for path in ("/wow/runs/not-a-uuid", "/wow/runs/not-a-uuid/manifest",
                 "/wow/runs/not-a-uuid/audit"):
        response = client.get(path, headers=auth_headers)
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "RUN_ID_INVALID"
    response = client.post("/wow/runs/not-a-uuid/cancel", headers=auth_headers)
    assert response.status_code == 422


def test_unpaired_unicode_candidate_fails_closed_before_redis_or_database(monkeypatch):
    """An internal model_construct caller can carry invalid Unicode."""
    def forbidden_io(*args, **kwargs):
        raise AssertionError("invalid candidate reached Redis or service-role DB")

    monkeypatch.setattr(agent_runtime_api, "get_client", forbidden_io)
    monkeypatch.setattr(agent_runtime_api, "_check_admission_rate_limit", forbidden_io)
    req = schemas.RunCreateRequest.model_construct(
        **{**_RUN_PAYLOAD, "candidate_inputs": [{"value": "\ud800"}]},
    )
    with pytest.raises(HTTPException) as exc:
        agent_runtime_api.create_run(req, idempotency_key="invalid-unicode")
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "RUN_CANDIDATE_PAYLOAD_TOO_LARGE"
    assert exc.value.detail["can_execute"] is False


def test_candidate_depth_limit_rejects_before_db(monkeypatch, auth_headers):
    def no_db():
        raise AssertionError("deep payload reached DB")
    monkeypatch.setattr(agent_runtime_api, "get_client", no_db)
    nested = {}
    for _ in range(agent_runtime_api.MAX_RUN_CANDIDATE_DEPTH + 2):
        nested = {"child": nested}
    response = client.post("/wow/runs", json={**_RUN_PAYLOAD, "candidate_inputs": [nested]},
        headers={**auth_headers, "Idempotency-Key": "too-deep"})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "RUN_CANDIDATE_COMPLEXITY_EXCEEDED"


def test_existing_redis_global_admission_limit_and_outage_fail_closed(monkeypatch):
    rate = agent_runtime_api._admission_redis(os.environ["REDIS_URL"])
    assert rate is agent_runtime_api._admission_redis(os.environ["REDIS_URL"])
    for i in range(agent_runtime_api.MAX_RUN_ADMISSIONS_PER_MINUTE):
        agent_runtime_api._check_admission_rate_limit(
            idempotency_key=f"unique-{i}", request_hash="same-body",
        )
    # Same-key retries are idempotent even at full capacity.
    agent_runtime_api._check_admission_rate_limit(
        idempotency_key="unique-0", request_hash="same-body",
    )
    assert len(rate.admissions) == agent_runtime_api.MAX_RUN_ADMISSIONS_PER_MINUTE
    with pytest.raises(HTTPException) as exc:
        agent_runtime_api._check_admission_rate_limit(
            idempotency_key="over-limit", request_hash="same-body",
        )
    assert exc.value.status_code == 429
    assert exc.value.detail["code"] == "AGENT_RUNTIME_ADMISSION_RATE_LIMITED"
    # No fixed-minute rollover burst: 59.999 seconds is still in-window.
    rate.now_ms = 59_999
    with pytest.raises(HTTPException) as boundary:
        agent_runtime_api._check_admission_rate_limit(
            idempotency_key="boundary-new", request_hash="same-body",
        )
    assert boundary.value.status_code == 429
    rate.now_ms = 60_000
    agent_runtime_api._check_admission_rate_limit(
        idempotency_key="after-window", request_hash="same-body",
    )
    assert len(rate.admissions) == 1
    monkeypatch.delenv("REDIS_URL")
    with pytest.raises(HTTPException) as unavailable:
        agent_runtime_api._check_admission_rate_limit(
            idempotency_key="unavailable", request_hash="same-body",
        )
    assert unavailable.value.status_code == 503


def test_cached_redis_pool_rotates_with_url(monkeypatch):
    # A changed Redis URL must never return the old process-local connection.
    import redis
    made = []

    def factory(url, **kwargs):
        connection = object()
        made.append((url, connection))
        return connection

    monkeypatch.setattr(redis.Redis, "from_url", factory)
    agent_runtime_api._admission_redis.cache_clear()
    try:
        first = agent_runtime_api._admission_redis("redis://first/0")
        assert first is agent_runtime_api._admission_redis("redis://first/0")
        second = agent_runtime_api._admission_redis("redis://rotated/0")
        assert second is not first
        assert [url for url, _ in made] == ["redis://first/0", "redis://rotated/0"]
    finally:
        agent_runtime_api._admission_redis.cache_clear()


def test_create_run_rejects_can_execute_true(monkeypatch, auth_headers):
    monkeypatch.setattr(agent_runtime_api, "get_client", lambda: FakeSupabaseClient())
    response = client.post(
        "/wow/runs", json={**_RUN_PAYLOAD, "can_execute": True},
        headers={"Idempotency-Key": "should-not-matter", **auth_headers},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "EXECUTION_PROHIBITED"


def test_create_run_requires_idempotency_key_header(monkeypatch, auth_headers):
    monkeypatch.setattr(agent_runtime_api, "get_client", lambda: FakeSupabaseClient())
    response = client.post("/wow/runs", json=_RUN_PAYLOAD, headers=auth_headers)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_create_run_happy_path_returns_202_and_pollable_run_id(monkeypatch, auth_headers):
    fake = FakeSupabaseClient()
    monkeypatch.setattr(agent_runtime_api, "get_client", lambda: fake)
    monkeypatch.setattr("ledger.get_client", lambda: fake)
    response = client.post("/wow/runs", json=_RUN_PAYLOAD, headers={"Idempotency-Key": "run-happy-1", **auth_headers})
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "COMPLETED"
    assert body["terminal"] is True
    assert body["reused"] is False
    assert body["can_execute"] is False
    assert body["poll_url"] == f"/wow/runs/{body['run_id']}/manifest"


def test_repeated_create_run_same_key_and_body_is_idempotent(monkeypatch, auth_headers):
    fake = FakeSupabaseClient()
    monkeypatch.setattr(agent_runtime_api, "get_client", lambda: fake)
    monkeypatch.setattr("ledger.get_client", lambda: fake)
    first = client.post("/wow/runs", json=_RUN_PAYLOAD, headers={"Idempotency-Key": "run-dup-1", **auth_headers})
    second = client.post("/wow/runs", json=_RUN_PAYLOAD, headers={"Idempotency-Key": "run-dup-1", **auth_headers})
    assert first.json()["run_id"] == second.json()["run_id"]
    assert second.json()["reused"] is True
    assert len(fake._store["wow_agent_runs"]) == 1
    # A genuine idempotent HTTP retry must not consume another rate slot.
    rate = agent_runtime_api._admission_redis(os.environ["REDIS_URL"])
    assert len(rate.admissions) == 1


def test_get_run_not_found_is_404(monkeypatch, auth_headers):
    monkeypatch.setattr(agent_runtime_api, "get_client", lambda: FakeSupabaseClient())
    response = client.get("/wow/runs/00000000-0000-0000-0000-000000000000", headers=auth_headers)
    assert response.status_code == 404


def test_nonterminal_manifest_is_never_reported_as_zero_picks_means_no_play(monkeypatch, auth_headers):
    fake = FakeSupabaseClient()
    monkeypatch.setattr(agent_runtime_api, "get_client", lambda: fake)
    created = _created_run(fake, "run-nonterminal-1")

    manifest = client.get(f"/wow/runs/{created['run_id']}/manifest", headers=auth_headers)
    assert manifest.status_code == 200
    body = manifest.json()
    assert body["terminal"] is False
    assert body["status"] == "CREATED"
    assert "reconciliation" not in body


def test_cancel_run_transitions_to_canceled(monkeypatch, auth_headers):
    fake = FakeSupabaseClient()
    monkeypatch.setattr(agent_runtime_api, "get_client", lambda: fake)
    created = _created_run(fake, "run-cancel-1")

    cancel = client.post(f"/wow/runs/{created['run_id']}/cancel", headers=auth_headers)
    assert cancel.status_code == 200
    assert cancel.json() == {"run_id": created["run_id"], "status": "CANCELED", "terminal": True, "can_execute": False}

    manifest = client.get(f"/wow/runs/{created['run_id']}/manifest", headers=auth_headers)
    assert manifest.json()["status"] == "CANCELED"
    assert manifest.json()["terminal"] is True


def test_cancel_already_terminal_run_is_a_no_op(monkeypatch, auth_headers):
    fake = FakeSupabaseClient()
    monkeypatch.setattr(agent_runtime_api, "get_client", lambda: fake)
    created = _created_run(fake, "run-cancel-2")
    client.post(f"/wow/runs/{created['run_id']}/cancel", headers=auth_headers)

    second_cancel = client.post(f"/wow/runs/{created['run_id']}/cancel", headers=auth_headers)
    assert second_cancel.status_code == 200
    assert second_cancel.json()["status"] == "CANCELED"


_RUN_WALK = [
    ("CREATED", "VALIDATING_REQUEST", "VALIDATE"),
    ("VALIDATING_REQUEST", "DISCOVERY_QUEUED", "DISCOVERY"),
    ("DISCOVERY_QUEUED", "DISCOVERY_RUNNING", "DISCOVERY"),
    ("DISCOVERY_RUNNING", "ROUTING", "ROUTING"),
    ("ROUTING", "RESEARCH_QUEUED", "RESEARCH"),
    ("RESEARCH_QUEUED", "RESEARCH_RUNNING", "RESEARCH"),
    ("RESEARCH_RUNNING", "EVIDENCE_QUEUED", "EVIDENCE"),
    ("EVIDENCE_QUEUED", "EVIDENCE_RUNNING", "EVIDENCE"),
    ("EVIDENCE_RUNNING", "MODELING_QUEUED", "MODELING"),
    ("MODELING_QUEUED", "MODELING_RUNNING", "MODELING"),
    ("MODELING_RUNNING", "AUDIT_QUEUED", "AUDIT"),
    ("AUDIT_QUEUED", "AUDIT_RUNNING", "AUDIT"),
    ("AUDIT_RUNNING", "FINAL_REFRESH", "FINAL_REFRESH"),
    ("FINAL_REFRESH", "RECONCILING", "RECONCILING"),
]


def test_synchronous_fake_worker_fixture_completes_with_balanced_reconciliation(monkeypatch, auth_headers):
    """Packet section 22, Phase 1 exit criterion, verbatim: a synchronous
    fake-worker fixture completes with balanced reconciliation.

    Three candidates, one required job each, run through a fake in-process
    worker (claim -> execute -> record output -> finish), reduced through
    the real deterministic reducer, reconciled through the real
    rows_in = completed + held + rejected invariant, and read back through
    the real GET /wow/runs/{id}/manifest endpoint. The run is built directly
    through the repository (see module docstring) so this test keeps manual
    control instead of racing against real eager-mode orchestration.
    """
    fake = FakeSupabaseClient()
    monkeypatch.setattr(agent_runtime_api, "get_client", lambda: fake)

    created = _created_run(fake, "run-fixture-1")
    run_id = created["run_id"]

    scenarios = [
        ("MLB:G1:PITCHER_KS", "wow.mlb-strikeout-expert", "FINAL_APPROVED", "SUCCEEDED"),
        ("MLB:G2:PITCHER_KS", "wow.mlb-strikeout-expert", "MODEL_UNAVAILABLE", "BLOCKED"),
        ("MLB:G3:PITCHER_KS", "wow.mlb-strikeout-expert", "MODEL_QUALIFIED_HOLD", "SUCCEEDED"),
    ]
    decisions = []
    for canonical_key, worker_id, ceiling, job_terminal_status in scenarios:
        candidate = repository.insert_candidate(
            fake, run_id=run_id, canonical_key=canonical_key, sport="MLB",
            participant="Test Pitcher", market_family="PLAYER_PROPS", period="FULL_GAME",
        )
        job, _ = repository.enqueue_job(
            fake, run_id=run_id, candidate_id=candidate["candidate_id"],
            worker_id=worker_id, worker_version="1.0.0",
            idempotency_key=f"job-{canonical_key}", required=True,
            input_hash=idempotency.input_hash({"canonical_key": canonical_key}),
        )

        claim = repository.try_transition_job(fake, job["job_id"], expected_status="QUEUED", next_status="RUNNING")
        assert claim.applied is True
        repository.record_job_output(
            fake, job_id=job["job_id"], run_id=run_id, candidate_id=candidate["candidate_id"],
            worker_id=worker_id, worker_version="1.0.0", contract_version="wow.agent-output.v1",
            output={"ceiling": ceiling}, output_hash=idempotency.input_hash({"ceiling": ceiling}),
        )
        finish = repository.try_transition_job(
            fake, job["job_id"], expected_status="RUNNING", next_status=job_terminal_status, ceiling=ceiling,
        )
        assert finish.applied is True
        duplicate_finish = repository.try_transition_job(
            fake, job["job_id"], expected_status="RUNNING", next_status=job_terminal_status, ceiling=ceiling,
        )
        assert duplicate_finish.applied is False

        decision = reduce_candidate(
            controlling_worker_id=worker_id,
            controlling_job_status=job_terminal_status,
            required_jobs=[RequiredJobResult(worker_id=worker_id, status=job_terminal_status, ceiling=ceiling)],
        )
        repository.set_candidate_terminal(
            fake, candidate["candidate_id"], terminal_label=decision.label,
            terminal_ceiling=decision.ceiling, blockers=list(decision.blockers),
            controlling_worker_id=worker_id,
        )
        decisions.append(decision)

    reconciliation = reconcile_from_ceilings(
        rows_in=len(decisions), terminal_ceilings=[d.ceiling for d in decisions],
    )
    assert reconciliation.status == "BALANCED"
    assert reconciliation.rows_completed == 1
    assert reconciliation.rows_held == 1
    assert reconciliation.rows_rejected == 1

    repository.set_run_row_counts(
        fake, run_id, rows_in=reconciliation.rows_in, rows_completed=reconciliation.rows_completed,
        rows_held=reconciliation.rows_held, rows_rejected=reconciliation.rows_rejected,
        reconciliation_status=reconciliation.status,
    )

    for current, next_state, stage in _RUN_WALK:
        result = repository.transition_run(fake, run_id, expected_status=current, next_status=next_state, stage=stage)
        assert result.applied is True
    final = repository.transition_run(
        fake, run_id, expected_status="RECONCILING", next_status="COMPLETED_WITH_BLOCKERS", stage="DONE",
    )
    assert final.applied is True

    manifest = client.get(f"/wow/runs/{run_id}/manifest", headers=auth_headers)
    assert manifest.status_code == 200
    body = manifest.json()
    assert body["terminal"] is True
    assert body["status"] == "COMPLETED_WITH_BLOCKERS"
    assert body["reconciliation"] == {
        "rows_in": 3, "rows_completed": 1, "rows_held": 1, "rows_rejected": 1, "balanced": True,
    }
    assert len(body["candidates"]) == 3
    assert body["can_execute"] is False
