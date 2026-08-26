from __future__ import annotations

import json
from pathlib import Path

from flask import Flask, jsonify

from gate_engine.runtime_attestation import install_flask_attestation, load_build_info


def test_load_build_info_valid_attestation(tmp_path: Path):
    path = tmp_path / "runtime_build_info.json"
    sha = "a" * 40
    path.write_text(json.dumps({
        "attestation_version": 1,
        "source_sha": sha,
        "source_ref": "main",
        "worktree_clean": True,
        "build_attested": True,
        "build_generated_at_utc": "2026-08-26T00:00:00+00:00",
    }), encoding="utf-8")

    info = load_build_info(path)
    assert info["source_sha"] == sha
    assert info["source_ref"] == "main"
    assert info["build_attested"] is True


def test_load_build_info_missing_or_malformed_fails_closed(tmp_path: Path):
    missing = load_build_info(tmp_path / "missing.json")
    assert missing["source_sha"] is None
    assert missing["build_attested"] is False

    bad_path = tmp_path / "bad.json"
    bad_path.write_text('{"source_sha":"not-a-sha","source_ref":"main","worktree_clean":true,"build_attested":true}', encoding="utf-8")
    bad = load_build_info(bad_path)
    assert bad["source_sha"] is None
    assert bad["build_attested"] is False


def test_health_and_ready_receive_immutable_attestation():
    app = Flask(__name__)
    info = {
        "source_sha": "b" * 40,
        "source_ref": "main",
        "build_attested": True,
        "attestation_version": 1,
    }
    install_flask_attestation(app, info)

    @app.get("/wow/engine/health")
    def health():
        return jsonify({"ok": True, "can_execute": False})

    @app.get("/wow/engine/ready")
    def ready():
        return jsonify({"ok": True, "can_execute": False})

    client = app.test_client()
    for path in ("/wow/engine/health", "/wow/engine/ready"):
        response = client.get(path)
        payload = response.get_json()
        assert response.status_code == 200
        assert payload["source_sha"] == "b" * 40
        assert payload["source_ref"] == "main"
        assert payload["build_attested"] is True
        assert payload["can_execute"] is False


def test_gunicorn_warmup_uses_supported_snapshot_accessor():
    root = Path(__file__).resolve().parents[2]
    text = (root / "gunicorn_conf.py").read_text(encoding="utf-8")
    assert "GovernanceSnapshot.instance()" not in text
    assert "get_snapshot_singleton().refresh()" in text


def test_build_script_generates_attestation_before_runtime():
    root = Path(__file__).resolve().parents[2]
    text = (root / "build.sh").read_text(encoding="utf-8")
    assert "runtime_build_info.json" in text
    assert 'git("rev-parse", "HEAD")' in text
    assert 'git("branch", "--show-current")' in text
    assert "BUILD_ATTESTATION_FAILED" in text
