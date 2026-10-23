"""No-network regression tests for native WOW Gemini Sidecar installer."""
import importlib.util
import json
import tempfile
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
                worker.install(cfg, "project_abc", "no")
            self.assertFalse(cfg.exists())


if __name__ == "__main__":
    unittest.main()
