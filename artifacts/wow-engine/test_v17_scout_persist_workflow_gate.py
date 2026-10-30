"""Behavioral regression for the actual Scout workflow source-admission shell."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / ".github/workflows/wow-v17-scout-brain-persist.yml"
RESUME = ROOT / ".github/workflows/wow-v17-scout-persist-resume.yml"
DISCOVERY = "Nightly all-sport discovery and governed specialist handoff"
VERIFY = "Verify Multi-Scout contract"

def source_step(name):
    lines = SOURCE.read_text().splitlines()
    start = next(i for i, x in enumerate(lines) if x.strip() == "- name: " + name)
    run = next(i for i in range(start + 1, len(lines)) if lines[i] == "        run: |")
    stop = next((i for i in range(run + 1, len(lines)) if lines[i].startswith("      - name: ")), len(lines))
    return "\n".join(x[10:] for x in lines[run+1:stop] if x.startswith("          "))

class ScoutGateTest(unittest.TestCase):
    def verify(self, *, discovery, event="workflow_run", artifact=False, verify="success", path=".github/workflows/wow-v17-nightly-multiscout.yml", branch="main", source_conclusion="success"):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "output"
            summary = Path(d) / "summary"
            run = {"name":"wow-v17-nightly-multiscout","path":path,"head_branch":branch,"status":"completed","conclusion":source_conclusion,"head_repository":{"full_name":"testowner/testrepo"},"event":event}
            jobs = {"jobs":[{"name":VERIFY,"conclusion":verify},{"name":DISCOVERY,"conclusion":discovery}]}
            artifacts = {"artifacts":[{"name":"wow-v17-multiscout-wow-scout-1-discovery","expired":False,"created_at":"2026-10-30T04:00:00Z"}] if artifact else []}
            bash = """gh() { case "$2" in */jobs?*) printf '%s' "$MOCK_JOBS";; */artifacts?*) printf '%s' "$MOCK_ARTIFACTS";; */runs/*) printf '%s' "$MOCK_RUN";; *) return 77;; esac; };\n""" + source_step("Locate recoverable Scout discovery artifact")
            env = dict(os.environ, RUNNER_TEMP=d, GITHUB_OUTPUT=str(out), GITHUB_STEP_SUMMARY=str(summary), GITHUB_REPOSITORY="testowner/testrepo", SOURCE_RUN_ID="41006393769", MOCK_RUN=json.dumps(run), MOCK_JOBS=json.dumps(jobs), MOCK_ARTIFACTS=json.dumps(artifacts))
            p=subprocess.run(["bash","-euo","pipefail","-c",bash],env=env,text=True,capture_output=True)
            return p.returncode, out.read_text() if out.exists() else "", p.stderr, summary.read_text() if summary.exists() else ""

    def test_workflow_run_success_but_discovery_skipped_is_not_applicable(self):
        status,out,err,summary=self.verify(discovery="skipped")
        self.assertEqual(status,0,err)
        self.assertIn("present=false",out)
        self.assertIn("SCOUT_DISCOVERY_NOT_APPLICABLE",summary)

    def test_genuine_discovery_missing_artifact_is_hold(self):
        status,out,err,_=self.verify(discovery="success",artifact=False)
        self.assertNotEqual(status,0)
        self.assertIn("SCOUT_DISCOVERY_ARTIFACT_MISSING_HOLD",err)
        self.assertNotIn("present=true",out)

    def test_genuine_discovery_with_artifact_is_ready(self):
        status,out,err,_=self.verify(discovery="success",artifact=True)
        self.assertEqual(status,0,err)
        self.assertIn("present=true",out)

    def test_skipped_discovery_with_artifact_rejects_conflict(self):
        status,_,err,_=self.verify(discovery="skipped",artifact=True)
        self.assertNotEqual(status,0)
        self.assertIn("SCOUT_SKIPPED_DISCOVERY_ARTIFACT_CONFLICT_HOLD",err)

    def test_untrusted_source_path_fails_closed(self):
        status,_,err,_=self.verify(discovery="success",artifact=True,path=".github/workflows/attacker.yml")
        self.assertNotEqual(status,0)
        self.assertIn("SCOUT_SOURCE_PROVENANCE_INVALID_HOLD",err)

    def test_skipped_workflow_dispatch_is_not_silently_waived(self):
        status,_,err,_=self.verify(discovery="skipped",event="workflow_dispatch")
        self.assertNotEqual(status,0)
        self.assertIn("SCOUT_DISCOVERY_NOT_SUCCESSFUL_HOLD",err)

    def test_resume_is_guarded_on_selected_source(self):
        data=RESUME.read_text()
        self.assertIn("SCOUT_RECOVERY_NOT_APPLICABLE",data)
        for name in ("Verify recoverable discovery artifact exists", "Capture persistence receipt baseline", "Explicitly resume Scout persistence", "Verify persistence receipt appears"):
            start=data.index("      - name: "+name)
            next_step=data.find("      - name:",start+10)
            section=data[start:next_step if next_step>=0 else None]
            self.assertIn("steps.source.outputs.run_id != ''",section)

if __name__ == "__main__":
    unittest.main()
