"""No-network regression tests for native WOW Gemini Sidecar installer."""
import importlib.util
import json
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "wow_gemini_sidecar.py"
spec = importlib.util.spec_from_file_location("wow_gemini_sidecar", SCRIPT)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class GeminiSidecarInstallerContract(unittest.TestCase):
    def test_preview_creates_no_files_and_uses_official_agentapi(self):
        with tempfile.TemporaryDirectory() as root:
            cfg = Path(root) / "config"
            proposed = worker.plan(cfg, "project_abc")
            self.assertFalse(cfg.exists())
            self.assertEqual(proposed["manifest"]["builtin"], "schedule")
            self.assertEqual(proposed["manifest"]["args"][1:3], ["agentapi", "new-conversation"])
            self.assertIn("can_execute=false", proposed["manifest"]["args"][3])
            self.assertIn("currently OPEN assigned canonical manifest incident", proposed["manifest"]["args"][3])
            self.assertIn("exact SHA", proposed["manifest"]["args"][3])
            self.assertNotIn("#1841", proposed["manifest"]["args"][3])
            self.assertIn("wow-flint-coding-handoff/SKILL.md", proposed["manifest"]["args"][3])
            self.assertIn("untrusted research", proposed["manifest"]["args"][3])
            self.assertIn("Never call Flint, OpenRouter", proposed["manifest"]["args"][3])

    def test_install_preserves_other_settings_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            cfg = Path(root) / "config"
            cfg.mkdir()
            existing = {"theme": "dark", "sidecars": {"owner-job": {"enabled": False}}}
            (cfg / "config.json").write_text(json.dumps(existing), encoding="utf-8")
            one = worker.install(cfg, "project_abc")
            two = worker.install(cfg, "project_abc")
            updated = json.loads((cfg / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(updated["theme"], "dark")
            self.assertEqual(updated["sidecars"]["owner-job"], existing["sidecars"]["owner-job"])
            self.assertEqual(updated["sidecars"][worker.SIDECAR_ID], {"enabled": True, "projectId": "project_abc"})
            self.assertEqual(one["manifest"], two["manifest"])
            self.assertEqual(worker.status(cfg)["manifest_exists"], True)
            self.assertEqual(worker.status(cfg)["runtime_verified"], False)

    def test_refuses_to_redirect_existing_sidecar(self):
        with tempfile.TemporaryDirectory() as root:
            cfg = Path(root) / "config"
            worker.install(cfg, "project_abc")
            with self.assertRaisesRegex(ValueError, "different project"):
                worker.install(cfg, "project_xyz")
            self.assertEqual(worker.status(cfg)["project_id"], "project_abc")

    def test_refuses_to_overwrite_modified_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            cfg = Path(root) / "config"
            worker.install(cfg, "project_abc")
            manifest = cfg / "sidecars" / worker.SIDECAR_ID / "sidecar.json"
            manifest.write_text('{"command": "other"}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
                worker.install(cfg, "project_abc")
            self.assertEqual(json.loads(manifest.read_text(encoding="utf-8"))["command"], "other")

    def test_malformed_config_fails_before_writing(self):
        with tempfile.TemporaryDirectory() as root:
            cfg = Path(root) / "config"
            cfg.mkdir()
            (cfg / "config.json").write_text("{broken", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                worker.install(cfg, "project_abc")
            self.assertFalse((cfg / "sidecars").exists())

    def test_invalid_inputs_do_not_write(self):
        with tempfile.TemporaryDirectory() as root:
            cfg = Path(root) / "config"
            with self.assertRaises(ValueError):
                worker.install(cfg, "your-project-id")
            with self.assertRaises(ValueError):
                worker.install(cfg, "ACTUAL_PROJECT_ID")
            with self.assertRaises(ValueError):
                worker.install(cfg, "project_abc", "no")
            self.assertFalse(cfg.exists())


    def test_google_documented_cron_tz_schedule_roundtrip(self):
        with tempfile.TemporaryDirectory() as root:
            cfg = Path(root) / "config"
            cron = "CRON_TZ=America/Chicago 17 8 * * *"
            preview = worker.plan(cfg, "project_abc", cron)
            self.assertEqual(preview["manifest"]["args"][0], cron)
            self.assertFalse(cfg.exists())
            installed = worker.install(cfg, "project_abc", cron)
            self.assertEqual(installed["manifest"]["args"][0], cron)
            self.assertFalse(worker.status(cfg)["runtime_verified"])

    def test_google_documented_cron_tz_utc_supported(self):
        self.assertEqual(worker.manifest("CRON_TZ=UTC 17 8 * * *")["args"][0],
                         "CRON_TZ=UTC 17 8 * * *")

    def test_invalid_cron_tz_fails_closed_without_writing(self):
        for cron in ("CRON_TZ=America/DefinitelyInvalid 17 8 * * *",
                     "CRON_TZ= 17 8 * * *",
                     "CRON_TZ=America/Chicago 17 8 * *",
                     "CRON_TZ=UTC 17 8 * * $(echo fail)"):
            with self.subTest(cron=cron), tempfile.TemporaryDirectory() as root:
                cfg = Path(root) / "config"
                with self.assertRaises(ValueError):
                    worker.install(cfg, "project_abc", cron)
                self.assertFalse(cfg.exists())

    def test_invalid_cron_ranges_and_zero_steps_never_write(self):
        for cron in ("99 99 * * *", "61 8 * * *", "17 24 * * *",
                     "17 8 0 * *", "17 8 * 13 *", "17 8 * * 9",
                     "*/0 8 * * *", "17 8 * * 1-8", "17 8 * * 3-1",
                     "17 8 * * 1,", "17 8 * * 1//2", "17 8 * * 1-1-1"):
            with self.subTest(cron=cron), tempfile.TemporaryDirectory() as root:
                cfg = Path(root) / "config"
                with self.assertRaises(ValueError):
                    worker.install(cfg, "project_abc", cron)
                self.assertFalse(cfg.exists())

    def test_valid_cron_composites_remain_supported(self):
        for cron in ("17 8 * * 1-5", "*/15 0,12 * 1-12 0,7",
                     "CRON_TZ=America/Chicago 0 8-17/2 * * 1-5"):
            with self.subTest(cron=cron), tempfile.TemporaryDirectory() as root:
                cfg = Path(root) / "config"
                self.assertEqual(worker.plan(cfg, "project_abc", cron)["cron"], cron)
                self.assertFalse(cfg.exists())

    def test_concurrent_installs_cannot_redirect_project(self):
        original_plan = worker.plan
        barrier = threading.Barrier(2)
        seen = set()
        guard = threading.Lock()

        def interleaved_plan(cfg, pid, cron=worker.DEFAULT_CRON):
            with guard:
                first = threading.get_ident() not in seen
                seen.add(threading.get_ident())
            if first:
                result = original_plan(cfg, pid, cron)
                barrier.wait(timeout=5)
                return result
            return original_plan(cfg, pid, cron)

        worker.plan = interleaved_plan
        try:
            with tempfile.TemporaryDirectory() as root:
                cfg = Path(root) / "config"
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(worker.install, cfg, pid)
                               for pid in ("project_aaa", "project_bbb")]
                    outcomes = []
                    for future in futures:
                        try:
                            outcomes.append(("ok", future.result(timeout=8)["project_id"]))
                        except ValueError as exc:
                            outcomes.append(("hold", str(exc)))
                successful = [row[1] for row in outcomes if row[0] == "ok"]
                self.assertEqual(len(successful), 1, outcomes)
                self.assertEqual(worker.status(cfg)["project_id"], successful[0])
        finally:
            worker.plan = original_plan

    def test_missing_writer_lease_limits_agent_to_readonly_proposal(self):
        prompt = worker.AGENT_PROMPT
        self.assertIn("Without a verified current authorized shared-branch writer lease", prompt)
        self.assertIn("read-only diagnosis and patch proposal", prompt)
        self.assertNotIn("make changes only in an isolated local worktree", prompt)

if __name__ == "__main__":
    unittest.main()
