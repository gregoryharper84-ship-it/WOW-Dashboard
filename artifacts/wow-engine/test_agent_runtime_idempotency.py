from agent_runtime.idempotency import (
    compute_request_hash,
    input_hash,
    job_key,
    prediction_key,
    run_key,
)


def test_request_hash_stable_across_key_order():
    a = compute_request_hash({"sport": "MLB", "line": 5.5, "sports": ["MLB", "WNBA"]})
    b = compute_request_hash({"sports": ["MLB", "WNBA"], "line": 5.5, "sport": "MLB"})
    assert a == b


def test_request_hash_changes_with_content():
    a = compute_request_hash({"sport": "MLB"})
    b = compute_request_hash({"sport": "WNBA"})
    assert a != b


def test_run_key_stable_and_sensitive_to_both_inputs():
    k1 = run_key("caller-abc", "hash1")
    k2 = run_key("caller-abc", "hash1")
    k3 = run_key("caller-abc", "hash2")
    k4 = run_key("caller-xyz", "hash1")
    assert k1 == k2
    assert k1 != k3
    assert k1 != k4


def test_job_key_sensitive_to_every_component():
    base = dict(run_id="r1", candidate_id="c1", worker_id="w1", worker_version="1.0.0", input_hash="h1")
    baseline = job_key(**base)
    for field, other_value in [
        ("run_id", "r2"), ("candidate_id", "c2"), ("worker_id", "w2"),
        ("worker_version", "2.0.0"), ("input_hash", "h2"),
    ]:
        varied = dict(base)
        varied[field] = other_value
        assert job_key(**varied) != baseline, f"job_key insensitive to {field}"


def test_job_key_stable_with_null_candidate_id():
    a = job_key(run_id="r1", candidate_id=None, worker_id="w1", worker_version="1.0.0", input_hash="h1")
    b = job_key(run_id="r1", candidate_id=None, worker_id="w1", worker_version="1.0.0", input_hash="h1")
    assert a == b


def test_prediction_key_sensitive_to_side():
    base = dict(
        candidate_id="c1", evidence_snapshot_id="e1", artifact_id="a1",
        calibrator_id="cal1", exact_line=5.5,
    )
    more = prediction_key(side="MORE", **base)
    less = prediction_key(side="LESS", **base)
    assert more != less


def test_input_hash_deterministic():
    payload = {"b": 2, "a": 1}
    assert input_hash(payload) == input_hash({"a": 1, "b": 2})
