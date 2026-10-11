"""Regression: Claude engineering worker persists a durable Supabase attempt receipt (Charter §8).

Executes the worker's actual "Persist durable attempt receipt to Supabase" Bash, which runs the
real v17/engineering_receipts.py writer (pinned copy) against a local fake PostgREST server.
No provider, GitHub or production Supabase is contacted.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from textwrap import dedent

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/wow-v17-claude-engineering-worker.yml"
WRITER = ROOT / "artifacts/wow-engine/v17/engineering_receipts.py"
PIN = "      - name: Pin trusted receipt writer from protected main\n"
DB = "      - name: Persist durable attempt receipt to Supabase\n"
GH_RECEIPT = "      - name: Append incident delivery receipt\n"
SHA_HEAD = "b" * 40
SHA_BASE = "a" * 40
KEY = "service-role-test-key-not-real"


def _step(name_line):
    source = WORKFLOW.read_text()
    lines = []
    for line in source.split(name_line, 1)[1].splitlines():
        if line.strip() and len(line) - len(line.lstrip()) <= 6:
            break
        lines.append(line)
    return "\n".join(lines) + "\n"


def _script(name_line):
    return dedent(_step(name_line).split("        run: |\n", 1)[1])


# --- fake PostgREST ---------------------------------------------------------


class FakePostgrest:
    def __init__(self, *, insert_status=201, mutate_readback=None):
        self.insert_status = insert_status
        self.mutate_readback = mutate_readback
        self.rows = {}
        self.requests = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                sent = json.loads(self.rfile.read(length))
                server.requests.append(("POST", self.path, dict(self.headers), sent))
                if server.insert_status >= 400:
                    return self._send(server.insert_status, {"message": "rejected"})
                row = {**sent, "receipt_id": str(uuid.uuid4()), "can_execute": False,
                       "recorded_at": "2026-10-09T04:00:00+00:00"}
                server.rows[row["receipt_id"]] = row
                self._send(201, [row])

            def do_GET(self):
                server.requests.append(("GET", self.path, dict(self.headers), None))
                match = re.search(r"receipt_id=eq\.([0-9a-f-]+)", self.path)
                row = dict(server.rows.get(match.group(1), {})) if match else {}
                if row and server.mutate_readback:
                    server.mutate_readback(row)
                self._send(200, [row] if row else [])

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://localhost:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def inserted(self):
        posts = [r for r in self.requests if r[0] == "POST"]
        return posts[-1][3] if posts else None

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def postgrest():
    server = FakePostgrest()
    yield server
    server.close()


def _pin(tmp_path):
    pinned = tmp_path / "pinned" / "engineering_receipts.py"
    pinned.parent.mkdir()
    shutil.copy(WRITER, pinned)
    return pinned, hashlib.sha256(pinned.read_bytes()).hexdigest()


PR_ENV = {
    "INCIDENT": "1552", "CODE": "NONE", "DISPOSITION": "PR_CREATED",
    "NEXT_ACTION": "Independent trust-root/QA review of PR #1600 at exact head; no merge or deploy without authorization.",
    "REVISIT": "PR review outcome or new exact-head CI result.", "DELIVERY_OUTCOME": "success",
    "PR": "1600", "HEAD": SHA_HEAD, "REGRESSION_STATUS": "0",
    "LEASE_GROUP": "ENGINEERING", "LEASE_EPOCH": "42", "WORKER_ID": "github-actions:777:2:anthropic",
}
POLICY_ENV = {
    **PR_ENV, "CODE": "ACTIONABLE_REPAIR_POLICY_BOUNDARY", "DISPOSITION": "BLOCKED_WITH_EXACT_REASON",
    "NEXT_ACTION": "Protected reviewer/owner authorization change for risk R3; no automatic retry or provider failover.",
    "REVISIT": "Protected reviewer/owner authorization change only.", "DELIVERY_OUTCOME": "failure",
    "PR": "", "HEAD": "", "REGRESSION_STATUS": "",
}


def _exec(tmp_path, server, overrides=None, *, cwd=None, plant=None):
    pinned, digest = _pin(tmp_path)
    for name, text in (plant or {}).items():
        (pinned.parent / name).write_text(text)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SUPABASE_", "PYTHON"))}
    env.update({
        "RUNNER_TEMP": str(tmp_path), "GITHUB_RUN_ID": "777", "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_REPOSITORY_OWNER": "gregoryharper84-ship-it",
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.txt"),
        "SUPABASE_URL": server.url if server else "", "SUPABASE_SERVICE_ROLE_KEY": KEY,
        "WRITER_PATH": str(pinned), "WRITER_SHA256": digest, "WRITER_PYTHON": sys.executable,
        "BASE_SHA": SHA_BASE,
    })
    env.update(PR_ENV)
    env.update(overrides or {})
    result = subprocess.run(["bash", "-c", _script(DB)], env=env, text=True,
                            capture_output=True, check=False, cwd=cwd)
    summary = (tmp_path / "summary.txt").read_text() if (tmp_path / "summary.txt").exists() else ""
    return result, summary


# --- structure -------------------------------------------------------------


def test_writer_is_pinned_from_main_before_any_agent_or_implementation_checkout():
    source = WORKFLOW.read_text()
    pin = source.index(PIN)
    assert source.index("      - name: Checkout protected main") < pin
    assert pin < source.index("      - name: Engineering Lead agent")
    assert pin < source.index("      - name: Checkout implementation branch")
    assert "install -m 0444 artifacts/wow-engine/v17/engineering_receipts.py" in _step(PIN)


def test_db_receipt_runs_for_every_repair_after_the_github_receipt():
    source = WORKFLOW.read_text()
    assert source.index(GH_RECEIPT) < source.index(DB) < source.index("      - name: Upload dual-stream dispatch receipt")
    assert "if: always() && steps.lead.outputs.action == 'REPAIR'" in _step(DB)


def test_db_receipt_never_reads_agent_authored_tree_or_inlines_expressions():
    script = _step(DB).split("run: |", 1)[1]
    for forbidden in ("${{", "artifacts/", "./", "source ", "pip "):
        assert forbidden not in script, forbidden
    assert '"$WRITER_PYTHON" -I "$WRITER_PATH"' in script
    step = _step(DB)
    assert "SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}" in step
    assert "WRITER_SHA256: ${{ steps.receipt_writer.outputs.sha256 }}" in step


def test_in_run_qa_agent_is_never_recorded_as_independent_qa():
    step = _step(DB)
    assert "steps.qa.outputs" not in step
    assert "qa_decision" not in step.split("run: |", 1)[1].replace("qa_decision is never set", "")


# --- executed behaviour ------------------------------------------------------


def test_pr_created_attempt_is_persisted_with_exact_identity(tmp_path, postgrest):
    result, summary = _exec(tmp_path, postgrest)
    assert result.returncode == 0, result.stdout + result.stderr
    row = postgrest.inserted
    assert row == {
        "incident_id": "1552", "provider": "claude", "role": "implementer",
        "worker_run_id": "github-actions:777:2:anthropic", "lease_id": "ENGINEERING", "lease_epoch": 42,
        "pr_number": 1600, "base_sha": SHA_BASE, "head_sha": SHA_HEAD,
        "tests": [{"name": "deterministic_qa_regression", "exit_status": 0}],
        "disposition": "PR_CREATED", "retry_count": 1,
        "next_action": PR_ENV["NEXT_ACTION"], "revisit_trigger": PR_ENV["REVISIT"],
    }
    assert [r[0] for r in postgrest.requests] == ["POST", "GET"]
    assert "persisted and read back for #1552 (PR_CREATED / NONE)" in summary


def test_policy_boundary_is_blocked_with_typed_reason_owner_and_revisit(tmp_path, postgrest):
    result, _ = _exec(tmp_path, postgrest, POLICY_ENV)
    assert result.returncode == 0, result.stdout + result.stderr
    row = postgrest.inserted
    assert row["disposition"] == "BLOCKED_WITH_EXACT_REASON"
    assert row["typed_blocker"] == "ACTIONABLE_REPAIR_POLICY_BOUNDARY"
    assert row["blocker_owner"] == "gregoryharper84-ship-it"
    assert row["revisit_trigger"] == "Protected reviewer/owner authorization change only."
    for absent in ("pr_number", "head_sha", "qa_decision", "can_execute"):
        assert absent not in row
    assert row["tests"] == []


@pytest.mark.parametrize("code", ["ACTIONABLE_REPAIR_IMPLEMENTATION_FAILED", "ACTIONABLE_REPAIR_PR_MISSING"])
def test_unresolved_typed_failure_stays_open_with_its_exact_code(tmp_path, postgrest, code):
    result, _ = _exec(tmp_path, postgrest, {**POLICY_ENV, "CODE": code, "DISPOSITION": "UNRESOLVED_TYPED_FAILURE"})
    assert result.returncode == 0, result.stdout + result.stderr
    row = postgrest.inserted
    assert row["disposition"] == "IN_PROGRESS"
    assert row["typed_blocker"] == code


def test_unreported_delivery_gate_is_recorded_not_blank(tmp_path, postgrest):
    result, _ = _exec(tmp_path, postgrest, {**POLICY_ENV, "CODE": "", "DISPOSITION": "", "DELIVERY_OUTCOME": "cancelled"})
    assert result.returncode == 0, result.stdout + result.stderr
    row = postgrest.inserted
    assert row["disposition"] == "IN_PROGRESS"
    assert row["typed_blocker"] == "ACTIONABLE_REPAIR_DELIVERY_UNVERIFIED:cancelled"
    assert row["next_action"] == "Operator inspects why the delivery gate did not report."


def test_tampered_writer_is_never_executed(tmp_path, postgrest):
    result, _ = _exec(tmp_path, postgrest, {"WRITER_SHA256": "0" * 64})
    assert result.returncode != 0
    assert "::error::ACTIONABLE_REPAIR_DB_RECEIPT_WRITER_UNTRUSTED:" in result.stdout
    assert postgrest.requests == []


@pytest.mark.parametrize("where", ["working_directory", "beside_pinned_writer"])
def test_planted_module_is_never_imported_by_the_writer(tmp_path, postgrest, where):
    # An agent step shares the runner; a stdlib-shadowing module must not hijack the writer.
    marker = tmp_path / "planted-ran"
    planted = f"open({str(marker)!r}, 'w').write('x')\nraise SystemExit(9)\n"
    if where == "working_directory":
        tree = tmp_path / "agent-tree"
        tree.mkdir()
        (tree / "json.py").write_text(planted)
        result, _ = _exec(tmp_path, postgrest, cwd=tree)
    else:
        result, _ = _exec(tmp_path, postgrest, plant={"json.py": planted, "uuid.py": planted})
    assert result.returncode == 0, result.stdout + result.stderr
    assert not marker.exists()


@pytest.mark.parametrize(
    "server_kwargs, overrides, reason",
    [
        (None, {}, "RECEIPT_CONFIGURATION_MISSING"),
        ({"insert_status": 400}, {}, "RECEIPT_PERSISTENCE_REJECTED"),
        ({"insert_status": 503}, {}, "RECEIPT_PERSISTENCE_UNAVAILABLE"),
        ({"mutate_readback": lambda row: row.update(disposition="FIXED_AND_VERIFIED")}, {}, "RECEIPT_READBACK_MISMATCH"),
        ({"mutate_readback": lambda row: row.update(can_execute=True)}, {}, "RECEIPT_READBACK_MISMATCH"),
    ],
)
def test_unverified_persistence_fails_closed_with_typed_reason(tmp_path, server_kwargs, overrides, reason):
    server = FakePostgrest(**server_kwargs) if server_kwargs is not None else None
    try:
        result, summary = _exec(tmp_path, server, overrides)
    finally:
        if server:
            server.close()
    assert result.returncode != 0
    assert f"::error::ACTIONABLE_REPAIR_DB_RECEIPT_PERSIST_FAILED: incident=1552; reason={reason};" in result.stdout
    assert "persisted and read back" not in summary
    assert KEY not in result.stdout + result.stderr + summary


@pytest.mark.parametrize("incident", ["1552;id", "", "#1552"])
def test_invalid_incident_identity_is_typed(tmp_path, postgrest, incident):
    result, _ = _exec(tmp_path, postgrest, {"INCIDENT": incident})
    assert result.returncode != 0
    assert "::error::ACTIONABLE_REPAIR_RECEIPT_INCIDENT_INVALID:" in result.stdout
    assert postgrest.requests == []


def test_malformed_identity_values_are_dropped_not_sent(tmp_path, postgrest):
    result, _ = _exec(tmp_path, postgrest, {"HEAD": "not-a-sha", "BASE_SHA": "", "LEASE_EPOCH": "x", "PR": "abc",
                                            "CODE": "ACTIONABLE_REPAIR_HEAD_INVALID",
                                            "DISPOSITION": "UNRESOLVED_TYPED_FAILURE"})
    assert result.returncode == 0, result.stdout + result.stderr
    row = postgrest.inserted
    for absent in ("head_sha", "base_sha", "lease_epoch", "pr_number"):
        assert absent not in row


# --- dispatcher classification ------------------------------------------------


@pytest.mark.parametrize("code", ["ACTIONABLE_REPAIR_DB_RECEIPT_PERSIST_FAILED",
                                  "ACTIONABLE_REPAIR_DB_RECEIPT_WRITER_UNTRUSTED"])
def test_receipt_failures_are_typed_and_never_fail_over(code):
    from v17 import engineering_provider_failover as module

    # Real failed-step logs echo the script first; only the emitted ##[error] line is the outcome.
    log = _script(DB) + f"\n##[error]{code}: incident=1552; reason=RECEIPT_PERSISTENCE_UNAVAILABLE.\n"
    result = module.classify_provider_failure("anthropic", log).as_dict()
    assert result["code"] == code
    assert result["disposition"] == "UNRESOLVED_TYPED_FAILURE"
    assert result["failover_eligible"] is False
    assert result["can_execute"] is False
