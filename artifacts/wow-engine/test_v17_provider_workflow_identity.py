"""Execute the real provider-routing shell with bounded provider/CLI fixtures."""
import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest
import zipfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/wow-v17-engineering-provider-dispatcher.yml"


class ProviderWorkflowIdentityTests(unittest.TestCase):
    def route(self, source, *, auth=True, code="OPENAI_API_QUOTA_EXCEEDED",
              eligible=True, conclusion="failure", title="lease=GLOBAL incident=AUTO",
              secondary="success"):
        text = WORKFLOW.read_text()
        section = text.split("      - name: Resolve provider action", 1)[1]
        script = section.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0]
        script = textwrap.dedent(script)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            gh = bin_dir / "gh"
            gh.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = run ] && [ \"$2\" = list ]; then\n"
                "  printf '[{\"databaseId\":901,\"conclusion\":\"%s\"}]\\n' \"$FAKE_SECONDARY\"\n"
                "else printf 'fixture failed worker log\\n'; fi\n"
            )
            gh.chmod(0o755)
            python = bin_dir / "python"
            classification = json.dumps({"code":code, "failover_eligible":eligible})
            python.write_text("#!/bin/sh\nprintf '%s\\n' '" + classification + "'\n")
            python.chmod(0o755)
            output = root / "outputs"
            output.touch()
            env = dict(os.environ, PATH=str(bin_dir)+":"+os.environ["PATH"],
                       RUNNER_TEMP=str(root), GITHUB_OUTPUT=str(output),
                       GITHUB_RUN_ID="900", GITHUB_REPOSITORY="fixture/repo",
                       EVENT_NAME="workflow_run", SOURCE_WORKFLOW=source,
                       SOURCE_RUN_ID="123", SOURCE_CONCLUSION=conclusion,
                       SOURCE_DISPLAY_TITLE=title, MANUAL_TARGET_INCIDENT="",
                       MANUAL_LEASE_GROUP="", ANTHROPIC_API_KEY="fixture" if auth else "",
                       CLAUDE_CODE_OAUTH_TOKEN="", OPENAI_API_KEY="",
                       FAKE_SECONDARY=secondary)
            result = subprocess.run(["bash","-c",script], env=env,
                                    capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            receipt = json.loads((root/"provider-route.json").read_text())
            self.assertFalse(receipt["can_execute"])
            self.assertEqual(receipt["terminal_authority"],"V17_TERMINAL_REDUCER")
            self.assertEqual(receipt["parent_run_id"],"123")
            return receipt

    def test_static_and_observed_openai_identity_dispatch_claude(self):
        for source in ("wow-v17-chatgpt-engineering-worker",
                       "wow-v17-chatgpt-engineering-worker lease=GLOBAL incident=AUTO"):
            with self.subTest(source=source):
                receipt=self.route(source)
                self.assertEqual(receipt["action"],"DISPATCH")
                self.assertEqual(receipt["provider"],"anthropic")
                self.assertEqual(receipt["reason"],"OPENAI_API_QUOTA_EXCEEDED")
                self.assertNotIn("provider_generation_allowed", receipt)

    def test_missing_fallback_auth_is_fail_closed(self):
        receipt=self.route("wow-v17-chatgpt-engineering-worker lease=GLOBAL incident=AUTO",
                           auth=False)
        self.assertEqual(receipt["action"],"SURVIVAL")
        self.assertEqual(receipt["reason"],"OPENAI_API_QUOTA_EXCEEDED:ANTHROPIC_UNAVAILABLE_OR_FAILED")

    def test_latest_failed_secondary_is_not_retried(self):
        receipt=self.route("wow-v17-chatgpt-engineering-worker",
                           secondary="failure")
        self.assertEqual(receipt["action"],"SURVIVAL")
        self.assertEqual(receipt["provider"],"deterministic")
        self.assertEqual(receipt["reason"],
                         "OPENAI_API_QUOTA_EXCEEDED:ANTHROPIC_UNAVAILABLE_OR_FAILED")

    def test_non_provider_failure_never_dispatches_fallback(self):
        receipt=self.route("wow-v17-chatgpt-engineering-worker lease=GLOBAL incident=AUTO",
                           code="NON_PROVIDER_FAILURE",eligible=False)
        self.assertEqual(receipt["action"],"SURVIVAL")
        self.assertFalse(receipt["independent_fallback_allowed"])

    def test_governance_boundary_never_dispatches_independent_fallback(self):
        receipt=self.route("wow-v17-chatgpt-engineering-worker",
                           code="ACTIONABLE_REPAIR_POLICY_BOUNDARY",eligible=False)
        self.assertFalse(receipt["independent_fallback_allowed"])

    def test_exhausted_primary_and_secondary_admit_only_independent_fallback(self):
        receipt=self.route("wow-v17-chatgpt-engineering-worker", secondary="failure")
        self.assertTrue(receipt["independent_fallback_allowed"])
        self.assertEqual(receipt["cursor_status"], "CURSOR_NOT_CONFIGURED")

    def test_claude_failure_does_not_loop_back_to_openai(self):
        receipt=self.route("wow-v17-claude-engineering-worker lease=GLOBAL incident=AUTO",
                           code="ANTHROPIC_API_AUTH_FAILED",eligible=False)
        self.assertEqual(receipt["action"],"SURVIVAL")
        self.assertEqual(receipt["reason"],"ANTHROPIC_API_AUTH_FAILED")

    def test_unknown_and_lookalike_names_remain_unknown(self):
        for source in ("unrelated-workflow lease=GLOBAL incident=AUTO",
                       "wow-v17-chatgpt-engineering-worker-lookalike"):
            with self.subTest(source=source):
                receipt=self.route(source)
                self.assertEqual(receipt["action"],"SURVIVAL")
                self.assertEqual(receipt["reason"],"UNKNOWN_ENGINEERING_WORKFLOW_FAILURE")

    def test_exact_target_and_domain_lease_propagate(self):
        receipt=self.route("wow-v17-chatgpt-engineering-worker lease=RUNTIME incident=1021",
                           title="lease=RUNTIME incident=1021")
        self.assertEqual(receipt["target_incident"],"1021")
        self.assertEqual(receipt["lease_group"],"RUNTIME")

    def test_success_does_not_start_another_worker(self):
        receipt=self.route("wow-v17-chatgpt-engineering-worker lease=GLOBAL incident=AUTO",
                           conclusion="success")
        self.assertEqual(receipt["action"],"NO_ACTION")

    def test_quota_and_failed_secondary_produce_typed_blocked_route_receipt(self):
        receipt = self.route(
            "wow-v17-chatgpt-engineering-worker",
            secondary="failure",
            title="lease=GLOBAL incident=2034",
        )
        self.assertEqual(receipt["action"], "SURVIVAL")
        self.assertEqual(
            receipt["reason"],
            "OPENAI_API_QUOTA_EXCEEDED:ANTHROPIC_UNAVAILABLE_OR_FAILED",
        )
        self.assertEqual(receipt["target_incident"], "2034")
        self.assertNotIn("status", receipt)
        self.assertNotIn("provider_generation_allowed", receipt)
        self.assertFalse(receipt["can_execute"])


class GeminiWorkerCompletionRouteTests(unittest.TestCase):
    def _run_gemini_route(self, *, conclusion="failure", issue_state="OPEN",
                          openai=True, anthropic=False, missing_artifact=False,
                          source_event="workflow_dispatch", primary_failure_log="",
                          artifact_variant="valid"):
        if not shutil.which("jq"):
            self.skipTest("Workflow shell fixture requires jq (present on GitHub Actions)")
        text = WORKFLOW.read_text()
        section = text.split("      - name: Resolve provider action", 1)[1]
        script = textwrap.dedent(section.split("        run: |\n", 1)[1]
                                 .split("\n      - name:", 1)[0])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture_root = root / "fixtures"
            fixture_root.mkdir()
            receipt_dir = fixture_root / "receipts"
            receipt_dir.mkdir()
            sha = "d129f421b8f4f21ec17fe6dec0a5b8195e210226"
            run_id = 40292121097
            attempt = 1
            repository = "gregoryharper84-ship-it/WOW-Dashboard"
            repository_id = 1240256887
            run = {
                "id": run_id,
                "run_attempt": attempt,
                "event": source_event,
                "path": ".github/workflows/wow-v17-gemini-engineering-worker.yml",
                "status": "completed",
                "conclusion": conclusion,
                "head_branch": "main",
                "head_sha": sha,
                "repository": {"id": repository_id, "full_name": repository},
                "head_repository": {"id": repository_id, "full_name": repository},
            }
            artifacts = {
                "total_count": 1,
                "artifacts": [{
                    "id": 12635928678,
                    "name": f"wow-gemini-candidate-{run_id}-{attempt}",
                    "expired": False,
                    "digest": "",
                    "workflow_run": {
                        "id": run_id,
                        "repository_id": repository_id,
                        "head_repository_id": repository_id,
                        "head_branch": "main",
                        "head_sha": sha,
                    },
                }],
            }
            manifest = {
                "can_execute": False,
                "terminal_authority": "V17_TERMINAL_REDUCER",
                "restoration": [{
                    "issue_number": 1951,
                    "severity": "P0",
                    "priority_rank": 1,
                    "execution_lane": "RAPID",
                    "rapid_stream": "NCAAF_RESTORE",
                    "lease_group": "P0_NCAAF_RESTORE",
                    "conflict_keys": ["NCAAF_MONEYLINE"],
                }],
                "acceleration": [],
            }
            issues = [{
                "number": 1951,
                "title": "NCAAF restoration",
                "body": "Issue content does not authorize routing.",
                "state": issue_state,
            }]
            selection = {
                "status": "SELECTED",
                "code": "EXISTING_CONDUCTOR_RESEARCH_ONLY",
                "incident_id": "1951",
                "issue_number": 1951,
                "title": "NCAAF restoration",
                "lease_group": "P0_NCAAF_RESTORE",
                "severity": "P0",
                "execution_lane": "RAPID",
                "writer_lease_granted": False,
                "github_branch_write_allowed": False,
                "can_execute": False,
                "terminal_authority": "V17_TERMINAL_REDUCER",
            }
            receipts = {
                "admission.json": {
                    "status": "ADMITTED", "code": "ADMITTED", "provider": "gemini",
                    "can_execute": False, "terminal_authority": "V17_TERMINAL_REDUCER",
                },
                "selection.json": selection,
                "workspace.json": {
                    "status": "TRUSTED", "code": "GEMINI_WORKSPACE_TRUSTED",
                    "can_execute": False, "terminal_authority": "V17_TERMINAL_REDUCER",
                },
                "model-receipt.json": {
                    "status": "HELD", "code": "GEMINI_API_UPSTREAM_UNAVAILABLE",
                    "exit_code": 247, "source_head_sha": sha, "retry_allowed": False,
                    "can_execute": False, "terminal_authority": "V17_TERMINAL_REDUCER",
                },
            }
            if conclusion == "success":
                receipts["model-receipt.json"] = {
                    "status": "COMPLETED", "code": "GEMINI_CLI_COMPLETE",
                    "exit_code": 0, "source_head_sha": sha, "retry_allowed": False,
                    "can_execute": False, "terminal_authority": "V17_TERMINAL_REDUCER",
                }
                receipts["patch-inspection.json"] = {
                    "status": "CANDIDATE_ONLY", "can_execute": False,
                }
                receipts["regression-receipt.json"] = {
                    "status": "TESTED_CANDIDATE", "code": "GEMINI_REGRESSION_PASS",
                    "exit_code": 0, "can_execute": False,
                }
                receipts["files.json"] = ["artifacts/wow-engine/v17/example.py"]
                receipts["candidate.patch"] = "diff --git a/example.py b/example.py\n"
            for name, value in receipts.items():
                path = receipt_dir / name
                path.write_text(value if isinstance(value, str) else json.dumps(value))
            artifact_zip = fixture_root / "artifact.zip"
            with zipfile.ZipFile(artifact_zip, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(receipt_dir.iterdir()):
                    archive.write(path, path.name)
            artifacts["artifacts"][0]["digest"] = (
                "sha256:" + hashlib.sha256(artifact_zip.read_bytes()).hexdigest()
            )
            if artifact_variant == "wrong_attempt":
                artifacts["artifacts"][0]["name"] = (
                    f"wow-gemini-candidate-{run_id}-{attempt + 1}"
                )
            elif artifact_variant == "duplicate":
                artifacts["artifacts"].append({
                    **artifacts["artifacts"][0],
                    "id": 12635928679,
                })
                artifacts["total_count"] = 2
            elif artifact_variant == "cross_repository":
                artifacts["artifacts"][0]["workflow_run"]["repository_id"] += 1
            elif artifact_variant == "paginated":
                artifacts["total_count"] = 101
            elif artifact_variant == "digest_mismatch":
                artifacts["artifacts"][0]["digest"] = "sha256:" + "0" * 64
            elif artifact_variant == "missing":
                artifacts["total_count"] = 0
                artifacts["artifacts"] = []
            (fixture_root / "run.json").write_text(json.dumps(run))
            (fixture_root / "artifacts.json").write_text(json.dumps(artifacts))
            (fixture_root / "issues.json").write_text(json.dumps(issues))
            (fixture_root / "manifest.json").write_text(json.dumps(manifest))

            bin_dir = root / "bin"
            bin_dir.mkdir()
            gh = bin_dir / "gh"
            gh.write_text(
                "#!/bin/bash\n"
                "if [ \"$1\" = api ]; then\n"
                "  case \"$2\" in\n"
                "    *\"/actions/runs/40292121097/artifacts?per_page=100\") "
                "cat \"$FIXTURE_ROOT/artifacts.json\" ;;\n"
                "    *\"/actions/artifacts/12635928678/zip\") "
                "[ \"$FIXTURE_MISSING_ARTIFACT\" != true ] && cat \"$FIXTURE_ROOT/artifact.zip\" ;;\n"
                "    *\"/actions/runs/40292121097\") cat \"$FIXTURE_ROOT/run.json\" ;;\n"
                "    *) exit 70 ;;\n"
                "  esac\n"
                "elif [ \"$1\" = issue ] && [ \"$2\" = list ]; then\n"
                "  cat \"$FIXTURE_ROOT/issues.json\"\n"
                "elif [ \"$1\" = run ] && [ \"$2\" = view ]; then\n"
                "  printf '%s\\n' \"$FIXTURE_PRIMARY_FAILURE_LOG\"\n"
                "elif [ \"$1\" = run ] && [ \"$2\" = list ]; then\n"
                "  if [[ \"$*\" == *wow-v17-chatgpt-engineering-worker.yml* ]] "
                "&& [ -n \"$FIXTURE_PRIMARY_FAILURE_LOG\" ]; then\n"
                "    printf '[{\"databaseId\":555,\"conclusion\":\"failure\",\"updatedAt\":\"%s\"}]\\n' \"$FIXTURE_UPDATED_AT\"\n"
                "  elif [[ \"$*\" == *wow-v17-claude-engineering-worker.yml* ]] "
                "&& [ \"$FIXTURE_SECONDARY_CONCLUSION\" != none ]; then\n"
                "    printf '[{\"databaseId\":556,\"conclusion\":\"%s\"}]\\n' \"$FIXTURE_SECONDARY_CONCLUSION\"\n"
                "  else printf '[]\\n'; fi\n"
                "else exit 70; fi\n"
            )
            gh.chmod(0o755)
            output = root / "github-output"
            output.touch()
            env = dict(
                os.environ,
                PATH=str(bin_dir) + ":" + os.environ["PATH"],
                RUNNER_TEMP=str(root),
                GITHUB_OUTPUT=str(output),
                GITHUB_RUN_ID="900",
                GITHUB_REPOSITORY=repository,
                EVENT_NAME="workflow_run",
                SOURCE_WORKFLOW="wow-v17-gemini-engineering-worker",
                SOURCE_RUN_ID=str(run_id),
                SOURCE_RUN_ATTEMPT=str(attempt),
                SOURCE_CONCLUSION="failure",
                SOURCE_DISPLAY_TITLE="lease=GLOBAL incident=9999",
                MANUAL_TARGET_INCIDENT="",
                MANUAL_LEASE_GROUP="",
                FORCE_PROVIDER="auto",
                OPENAI_API_KEY="test-key" if openai else "",
                ANTHROPIC_API_KEY="test-key" if anthropic else "",
                CLAUDE_CODE_OAUTH_TOKEN="",
                GEMINI_ENGINEERING_ENABLED="true",
                GEMINI_AUTO_ROUTING_ENABLED="false",
                GEMINI_SPEND_CAP_CONFIRMED="true",
                FIXTURE_ROOT=str(fixture_root),
                FIXTURE_MISSING_ARTIFACT="true" if missing_artifact else "false",
                FIXTURE_PRIMARY_FAILURE_LOG=primary_failure_log,
                FIXTURE_UPDATED_AT=datetime.now(timezone.utc).isoformat(),
                FIXTURE_SECONDARY_CONCLUSION="success" if anthropic else "none",
            )
            result = subprocess.run(
                ["bash", "-c", script], env=env, cwd=ROOT,
                capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            receipt = json.loads((root / "provider-route.json").read_text())
            self.assertFalse(receipt["can_execute"])
            self.assertEqual(receipt["terminal_authority"], "V17_TERMINAL_REDUCER")
            return receipt

    def test_verified_upstream_failure_routes_only_authorized_provider_and_exact_issue_lease(self):
        receipt = self._run_gemini_route()
        self.assertEqual((receipt["action"], receipt["provider"]), ("DISPATCH", "openai"))
        self.assertTrue(receipt["reason"].startswith("GEMINI_API_UPSTREAM_UNAVAILABLE:"))
        self.assertEqual(receipt["parent_run_id"], "40292121097")
        self.assertEqual(receipt["target_incident"], "1951")
        self.assertEqual(receipt["lease_group"], "P0_NCAAF_RESTORE")

    def test_success_and_invalid_child_evidence_never_dispatch_provider(self):
        success = self._run_gemini_route(conclusion="success")
        self.assertEqual(success["action"], "NO_ACTION")
        self.assertEqual(success["reason"], "GEMINI_TESTED_CANDIDATE_REQUIRES_INDEPENDENT_REVIEW")

        closed = self._run_gemini_route(issue_state="CLOSED")
        self.assertEqual((closed["action"], closed["provider"]), ("SURVIVAL", "deterministic"))
        self.assertEqual(closed["reason"], "GEMINI_CHILD_INCIDENT_NOT_CURRENTLY_OPEN")

        missing = self._run_gemini_route(missing_artifact=True, artifact_variant="missing")
        self.assertEqual((missing["action"], missing["provider"]), ("SURVIVAL", "deterministic"))
        self.assertEqual(missing["reason"], "GEMINI_CHILD_ARTIFACT_MISSING")

        missing_archive = self._run_gemini_route(missing_artifact=True)
        self.assertEqual((missing_archive["action"], missing_archive["provider"]),
                         ("SURVIVAL", "deterministic"))
        self.assertEqual(missing_archive["reason"], "GEMINI_CHILD_ARTIFACT_MISSING")

        for variant in (
            "wrong_attempt", "duplicate", "cross_repository", "paginated",
            "digest_mismatch",
        ):
            with self.subTest(artifact_variant=variant):
                invalid = self._run_gemini_route(artifact_variant=variant)
                self.assertEqual((invalid["action"], invalid["provider"]),
                                 ("SURVIVAL", "deterministic"))

        no_provider = self._run_gemini_route(openai=False, anthropic=False)
        self.assertEqual((no_provider["action"], no_provider["provider"]), ("SURVIVAL", "deterministic"))
        self.assertTrue(no_provider["reason"].startswith(
            "GEMINI_API_UPSTREAM_UNAVAILABLE:NO_HEALTHY_ENGINEERING_PROVIDER_AVAILABLE"
        ))

        wrong_source = self._run_gemini_route(source_event="push")
        self.assertEqual((wrong_source["action"], wrong_source["provider"]),
                         ("SURVIVAL", "deterministic"))
        self.assertEqual(wrong_source["reason"], "GEMINI_CHILD_SOURCE_PROVENANCE_MISMATCH")

    def test_existing_primary_http_402_and_429_breakers_do_not_retry_openai(self):
        for error in ("HTTP 402 payment required", "HTTP 429 Too Many Requests"):
            with self.subTest(error=error):
                receipt = self._run_gemini_route(primary_failure_log=error)
                self.assertEqual((receipt["action"], receipt["provider"]),
                                 ("SURVIVAL", "deterministic"))
                self.assertIn("CIRCUIT_BREAKER:OPENAI_API_", receipt["reason"])
                self.assertTrue(receipt["reason"].endswith(
                    "SECONDARY_UNAVAILABLE_OR_FAILED"
                ))

                fallback = self._run_gemini_route(
                    primary_failure_log=error, anthropic=True,
                )
                self.assertEqual((fallback["action"], fallback["provider"]),
                                 ("DISPATCH", "anthropic"))
                self.assertIn("CIRCUIT_BREAKER:OPENAI_API_", fallback["reason"])

    def test_gemini_recovery_keeps_remote_writer_and_p0_concurrency_limits(self):
        text = WORKFLOW.read_text()
        dispatcher = text.split("      - name: Dispatch OpenAI primary worker", 1)[1]
        fence = text.split("      - name: Fence unresolved remote writers", 1)[1]
        self.assertIn("id: remote_fence", fence.split("\n      - name:", 1)[0])
        self.assertIn("steps.remote_fence.outcome == 'success'", dispatcher)
        self.assertLess(text.index("Fence unresolved remote writers"),
                        text.index("Dispatch OpenAI primary worker"))
        p0_dispatch = (ROOT / ".github/workflows/wow-v17-p0-rapid-dispatch.yml").read_text()
        self.assertIn("max_parallel_writers: 3", p0_dispatch)
        self.assertIn("ACTIVE_WRITER_INVENTORY_TRUNCATED", p0_dispatch)
        self.assertIn("wow-v17-engineering-implementation-single-writer", text)


class ProviderControlPlaneHandoffTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text()

    def step_script(self, name):
        block = self.text.split(f"      - name: {name}\n", 1)[1]
        if name == "Surface degraded provider state":
            block = block.split("\n  independent-provider:", 1)[0]
        else:
            block = block.split("\n      - name:", 1)[0]
        return textwrap.dedent(block.split("        run: |\n", 1)[1])

    def run_step(self, name, env):
        return subprocess.run(
            ["bash", "-e", "-c", self.step_script(name)],
            env=dict(os.environ, **env),
            capture_output=True,
            text=True,
            timeout=10,
        )

    def route_receipt(self):
        return {
            "action": "SURVIVAL",
            "provider": "deterministic",
            "reason": "CIRCUIT_BREAKER:OPENAI_API_QUOTA_EXCEEDED:SECONDARY_UNAVAILABLE_OR_FAILED",
            "parent_run_id": "901",
            "target_incident": "2034",
            "lease_group": "P0_LLP_RESTORE",
            "cursor_status": "CURSOR_NOT_CONFIGURED",
            "independent_fallback_allowed": True,
            "can_execute": False,
            "terminal_authority": "V17_TERMINAL_REDUCER",
        }

    def validate(self, directory, *, route=None, survival=None,
                 missing_route=False, missing_survival=False, **overrides):
        route_path = Path(directory) / "provider-route.json"
        survival_dir = Path(directory) / "wow-survival"
        survival_dir.mkdir()
        survival_path = survival_dir / "provider-survival-receipt.json"
        if not missing_route:
            route_path.write_text(json.dumps(self.route_receipt() if route is None else route))
        if not missing_survival:
            survival_path.write_text(json.dumps(survival or {
                "status": "DEGRADED_DETERMINISTIC_SURVIVAL",
                "reason": self.route_receipt()["reason"],
                "parent_run_id": "901",
                "priority": {"incident_id": "PM-TEST-001"},
                "provider_generation_allowed": False,
                "deterministic_checks_allowed": True,
                "probability_authority": False,
                "can_execute": False,
                "terminal_authority": "V17_TERMINAL_REDUCER",
            }))
        env = {
            "RUNNER_TEMP": str(directory),
            "ROUTE_RECEIPT": str(route_path),
            "SURVIVAL_RECEIPT": str(survival_path),
            "ACTION": "SURVIVAL",
            "PROVIDER": "deterministic",
            "REASON": self.route_receipt()["reason"],
            "PARENT_RUN_ID": "901",
            "TARGET_INCIDENT": "2034",
            "LEASE_GROUP": "P0_LLP_RESTORE",
            "ROUTE_OUTCOME": "success",
            "REMOTE_FENCE_OUTCOME": "success",
            "SURVIVAL_OUTCOME": "success",
            "OPENAI_DISPATCH_OUTCOME": "skipped",
            "ANTHROPIC_DISPATCH_OUTCOME": "skipped",
            "GEMINI_DISPATCH_OUTCOME": "skipped",
        }
        env.update(overrides)
        return self.run_step("Validate provider-control receipts", env)

    def surface(self, **overrides):
        env = {
            "ACTION": "SURVIVAL",
            "REASON": self.route_receipt()["reason"],
            "ROUTE_OUTCOME": "success",
            "REMOTE_FENCE_OUTCOME": "success",
            "SURVIVAL_OUTCOME": "success",
            "OPENAI_DISPATCH_OUTCOME": "skipped",
            "ANTHROPIC_DISPATCH_OUTCOME": "skipped",
            "GEMINI_DISPATCH_OUTCOME": "skipped",
            "RECEIPT_VALIDATION_OUTCOME": "success",
            "RECEIPT_UPLOAD_OUTCOME": "success",
        }
        env.update(overrides)
        return self.run_step("Surface degraded provider state", env)

    def test_fully_recorded_quota_hold_is_router_healthy_but_not_repair_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            validated = self.validate(directory)
            self.assertEqual(validated.returncode, 0, validated.stderr)

        surfaced = self.surface()
        self.assertEqual(surfaced.returncode, 0, surfaced.stderr)
        self.assertIn("::warning::ROUTER_HEALTHY_PROVIDER_BLOCKED:", surfaced.stdout)
        self.assertNotIn("::error::", surfaced.stdout)

        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "summary.md"
            recorded = self.run_step(
                "Record provider-control summary",
                {
                    "GITHUB_STEP_SUMMARY": str(summary),
                    "ACTION": "SURVIVAL",
                    "PROVIDER": "deterministic",
                    "REASON": self.route_receipt()["reason"],
                    "PARENT_RUN_ID": "901",
                    "TARGET_INCIDENT": "2034",
                    "ROUTE_OUTCOME": "success",
                    "REMOTE_FENCE_OUTCOME": "success",
                    "SURVIVAL_OUTCOME": "success",
                    "OPENAI_DISPATCH_OUTCOME": "skipped",
                    "ANTHROPIC_DISPATCH_OUTCOME": "skipped",
                    "GEMINI_DISPATCH_OUTCOME": "skipped",
                    "RECEIPT_VALIDATION_OUTCOME": "success",
                    "RECEIPT_UPLOAD_OUTCOME": "success",
                },
            )
            self.assertEqual(recorded.returncode, 0, recorded.stderr)
            text = summary.read_text()
            self.assertIn("ROUTER_HEALTHY_PROVIDER_BLOCKED", text)
            self.assertIn("OPEN / BLOCKED_WITH_EXACT_REASON", text)
            self.assertIn("repair_verified: false", text)
            self.assertIn("provider_generation_allowed: false", text)
            self.assertIn("REPAIR_DELIVERED=false", text)
            self.assertIn("survival_receipt_status: DEGRADED_DETERMINISTIC_SURVIVAL", text)
            self.assertNotIn("FIXED_AND_VERIFIED", text)
            self.assertNotIn("PR_CREATED", text)

    def test_malformed_receipt_fence_survival_or_artifact_failure_stays_red(self):
        with tempfile.TemporaryDirectory() as directory:
            route = self.route_receipt()
            route["reason"] = "OTHER"
            result = self.validate(directory, route=route)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("PROVIDER_ROUTE_RECEIPT_INVALID", result.stdout)

        with tempfile.TemporaryDirectory() as directory:
            result = self.validate(directory, REMOTE_FENCE_OUTCOME="failure")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("REMOTE_WRITER_RECONCILIATION_REQUIRED", result.stdout)

        with tempfile.TemporaryDirectory() as directory:
            result = self.validate(directory, SURVIVAL_OUTCOME="failure")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("DETERMINISTIC_SURVIVAL_CHECKS_FAILED", result.stdout)

        with tempfile.TemporaryDirectory() as directory:
            result = self.validate(directory, missing_route=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("PROVIDER_ROUTE_RECEIPT_INVALID", result.stdout)

        with tempfile.TemporaryDirectory() as directory:
            result = self.validate(directory, missing_survival=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("PROVIDER_SURVIVAL_RECEIPT_INVALID", result.stdout)

        for outcomes in (
            {"REMOTE_FENCE_OUTCOME": "failure"},
            {"SURVIVAL_OUTCOME": "failure"},
            {"ACTION": "DISPATCH", "OPENAI_DISPATCH_OUTCOME": "failure"},
            {"RECEIPT_VALIDATION_OUTCOME": "failure"},
            {"RECEIPT_UPLOAD_OUTCOME": "failure"},
        ):
            with self.subTest(outcomes=outcomes):
                result = self.surface(**outcomes)
                self.assertEqual(result.returncode, 1)
                self.assertIn("::error::BLOCKED_WITH_EXACT_REASON:", result.stdout)

    def test_fallback_requires_enabled_eligible_route_and_is_separately_auditable(self):
        outputs = self.text.split("    outputs:\n", 1)[1].split("\n\n    steps:", 1)[0]
        self.assertIn("independent_fallback_allowed == 'true'", outputs)
        self.assertIn("WOW_COPILOT_FALLBACK_ENABLED == 'true'", outputs)
        self.assertIn("steps.remote_fence.outcome == 'success'", outputs)
        self.assertIn("steps.survival.outcome == 'success'", outputs)
        self.assertIn("steps.receipt_validation.outcome == 'success'", outputs)
        self.assertIn("steps.provider_receipt.outcome == 'success'", outputs)
        self.assertNotIn("|| steps.remote_fence.outcome == 'failure'", outputs)
        job = self.text.split("  independent-provider:\n", 1)[1]
        self.assertIn("needs.provider-control.outputs.fallback_required == 'true'", job)
        self.assertIn("needs.provider-control.outputs.cursor_dispatch == 'true'", job)
        self.assertNotIn("needs.provider-control.result == 'failure'", job)
        self.assertIn("group: wow-v17-engineering-implementation-single-writer", job)
        self.assertIn("engineering_remote_fallback.py fallback", job)
        self.assertIn("independent-provider-receipt.json", job)
        self.assertIn("if-no-files-found: error", job)
        self.assertIn("engineering_remote_fallback.py guard", self.text)

    def test_gemini_child_and_fallback_dispatch_never_claim_router_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "summary.md"
            result = self.run_step(
                "Record provider-control summary",
                {
                    "GITHUB_STEP_SUMMARY": str(summary),
                    "ACTION": "DISPATCH",
                    "PROVIDER": "openai",
                    "REASON": "GEMINI_API_UPSTREAM_UNAVAILABLE:OPENAI_PRIMARY_AVAILABLE",
                    "PARENT_RUN_ID": "40292121097",
                    "TARGET_INCIDENT": "1951",
                    "LEASE_GROUP": "P0_NCAAF_RESTORE",
                    "ROUTE_OUTCOME": "success",
                    "REMOTE_FENCE_OUTCOME": "success",
                    "SURVIVAL_OUTCOME": "skipped",
                    "OPENAI_DISPATCH_OUTCOME": "success",
                    "ANTHROPIC_DISPATCH_OUTCOME": "skipped",
                    "GEMINI_DISPATCH_OUTCOME": "skipped",
                    "RECEIPT_VALIDATION_OUTCOME": "success",
                    "RECEIPT_UPLOAD_OUTCOME": "success",
                },
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = summary.read_text()
            self.assertIn("router_status: BLOCKED_WITH_EXACT_REASON", report)
            self.assertIn("provider_dispatch_acceptance_is_not_candidate_acceptance: true", report)
            self.assertNotIn("router_status: ROUTER_READY", report)


if __name__ == "__main__":
    unittest.main()
