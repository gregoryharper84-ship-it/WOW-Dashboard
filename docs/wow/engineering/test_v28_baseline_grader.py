"""One-shot fixture checks for V28 census. No production dependencies."""
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

spec=importlib.util.spec_from_file_location("v28_baseline_grader",Path(__file__).with_name("v28_baseline_grader.py"))
g=importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

class Indicators(unittest.TestCase):
    def test_reverts(self):
        self.assertTrue(g.indicators('Revert "fix: safety"')["revert_explicit"])
        self.assertTrue(g.indicators("undo unrelated\n\nThis reverts commit "+"a"*40+".")["revert_explicit"])
        self.assertFalse(g.indicators("not a Revert!")["revert_explicit"])

    def test_multiline_body_is_one_commit(self):
        item=g.parse_commit("a"*40+"\x1f2026-10-01T00:00:00+00:00\x1f"+"b"*40+"\x1ffix: first\n\nbecause cache was stale\nother paragraph")
        self.assertEqual(item["message"].count("\n"),3)
        self.assertTrue(g.indicators(item["message"])["mechanism_lexical"])

    def test_mechanism_not_temporal_proof(self):
        x=g.indicators("fix: code because old cursor starves next candidate")
        self.assertTrue(x["mechanism_lexical"])
        self.assertEqual(x["mechanism_before_first_edit"],"UNAVAILABLE_FROM_GIT")
        self.assertFalse(g.indicators("fix: improve retries")["mechanism_lexical"])

    def test_red_first_is_not_test_file_addition(self):
        self.assertEqual(g.indicators("test: added regression")["red_first"],"UNAVAILABLE_FROM_GIT")

    def test_diff_over_label(self):
        self.assertEqual(g.class_from_paths([".github/workflows/security.yml"]),"P")
        self.assertEqual(g.class_from_paths(["v17/calibration_model.py"]),"C_REVIEW_REQUIRED")
        self.assertEqual(g.class_from_paths(["tests/test_simple_adapter.py"]),"R")

    def test_same_cause_requires_code_path_and_72h(self):
        def item(sha,stamp,paths):
            return {"sha":sha,"committed_at":stamp,"paths":paths,"indicators":{"failure_codes":["QA_CHECK_FAILED"]}}
        a=item("a","2026-10-01T00:00:00+00:00",["x.py"])
        b=item("b","2026-10-03T23:00:00+00:00",["x.py"])
        self.assertTrue(g.same_cause_candidate(a,b))
        b["committed_at"]="2026-10-05T01:00:00+00:00"
        self.assertFalse(g.same_cause_candidate(a,b))
        b["committed_at"]="2026-10-03T23:00:00+00:00";b["paths"]=["y.py"]
        self.assertFalse(g.same_cause_candidate(a,b))

    def test_red_first_requires_matched_preedit_execution(self):
        fix="b"*40
        evidence={"pre":{"test_id":"test_queue_starvation","commit_sha":"a"*40,"exit_code":1,"executed_at":"2026-10-01T09:00:00+00:00"},
                  "post":{"test_id":"test_queue_starvation","commit_sha":fix,"exit_code":0,"executed_at":"2026-10-01T12:00:00+00:00"}}
        self.assertTrue(g.red_first_from_evidence(fix,"2026-10-01T10:00:00+00:00",evidence))
        bad={**evidence,"pre":{**evidence["pre"],"executed_at":"2026-10-01T11:00:00+00:00"}}
        self.assertFalse(g.red_first_from_evidence(fix,"2026-10-01T10:00:00+00:00",bad))
        with self.assertRaisesRegex(ValueError,"RED_FIRST_UNAVAILABLE"):
            g.red_first_from_evidence(fix,"2026-10-01T10:00:00+00:00",{})
        for key in ("pre", "post"):
            for malformed in (None, 123, []):
                broken = {**evidence, key: {**evidence[key], "executed_at": malformed}}
                with self.subTest(key=key, malformed=malformed):
                    self.assertFalse(g.red_first_from_evidence(
                        fix, "2026-10-01T10:00:00+00:00", broken
                    ))

    def test_git_utc_z_timestamp_rework_candidate(self):
        self.assertEqual(g.parse_time("2026-09-02T10:57:35Z"),
                         g.parse_time("2026-09-02T10:57:35+00:00"))
        a = {"sha": "a"*40, "committed_at": "2026-09-02T10:57:35Z",
             "paths": ["v17/worker.py"],
             "indicators": {"failure_codes": ["REMOTE_WRITER_RECONCILIATION_REQUIRED"]}}
        b = {"sha": "b"*40, "committed_at": "2026-09-03T10:57:35+00:00",
             "paths": ["v17/worker.py"],
             "indicators": {"failure_codes": ["REMOTE_WRITER_RECONCILIATION_REQUIRED"]}}
        self.assertTrue(g.same_cause_candidate(a, b))
        b["committed_at"] = "2026-09-06T10:57:36Z"
        self.assertFalse(g.same_cause_candidate(a, b))

    def test_window_is_inclusive(self):
        self.assertTrue(g.is_within("2026-08-06T00:00:00+00:00"))
        self.assertFalse(g.is_within("2026-08-05T23:59:59+00:00"))
        self.assertFalse(g.is_within("2026-11-04T14:07:06+00:00"))

class GitFixtures(unittest.TestCase):
    def test_census_not_line_count_and_revert(self):
        with tempfile.TemporaryDirectory() as root:
            def cmd(*args):
                return subprocess.run(["git",*args],cwd=root,capture_output=True,text=True,check=True).stdout.strip()
            cmd("init","-q");cmd("config","user.email","fixture@example.invalid");cmd("config","user.name","Fixture")
            def commit(val,message,date):
                Path(root,"test.txt").write_text(val)
                cmd("add","test.txt")
                env={**os.environ,"GIT_AUTHOR_DATE":date,"GIT_COMMITTER_DATE":date}
                subprocess.run(["git","commit","-qm",message],cwd=root,env=env,check=True)
                return cmd("rev-parse","HEAD")
            commit("a","fix: setup","2026-08-01T00:00:00+00:00")
            commit("b","fix: stale value because wrong cache\n\nQA_CHECK_FAILED","2026-08-08T00:00:00+00:00")
            head=commit("c",'Revert "fix: stale value"\n\nThis reverts commit '+"a"*40+".","2026-08-09T00:00:00+00:00")
            result=g.census(root,head)
            self.assertEqual(result["population_commits"],2)
            self.assertEqual(result["explicit_revert_counts"]["R"],1)
            self.assertEqual(result["mechanism_lexical_counts"]["R"],1)
            self.assertEqual(result["classes"]["R"],2)

    def test_merge_count_and_no_double_classification(self):
        with tempfile.TemporaryDirectory() as root:
            def cmd(*args):
                return subprocess.run(["git",*args],cwd=root,capture_output=True,text=True,check=True).stdout.strip()
            cmd("init","-q");cmd("config","user.email","fixture@example.invalid");cmd("config","user.name","Fixture")
            env={**os.environ,"GIT_AUTHOR_DATE":"2026-08-10T00:00:00+00:00","GIT_COMMITTER_DATE":"2026-08-10T00:00:00+00:00"}
            def commit(path,value):
                Path(root,path).write_text(value)
                cmd("add",path)
                subprocess.run(["git","commit","-qm","fix: fixture"],cwd=root,env=env,check=True)
            commit("a.txt","a")
            original=cmd("branch","--show-current")
            cmd("checkout","-qb","topic")
            commit("b.txt","b")
            cmd("checkout","-q",original)
            commit("c.txt","c")
            subprocess.run(["git","merge","--no-ff","-m","merge fixture","topic"],cwd=root,env=env,check=True,capture_output=True)
            result=g.census(root,cmd("rev-parse","HEAD"))
            self.assertEqual(result["merges"],1)
            self.assertEqual(result["classes"]["MERGE_UNCLASSIFIED"],1)
            self.assertEqual(result["population_commits"],4)

    def test_shallow_history_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            subprocess.run(["git","init","-q",root],check=True)
            Path(root,".git","shallow").write_text("a"*40+"\n")
            with self.assertRaisesRegex(RuntimeError,"SHALLOW_CLONE"):
                g.census(root)


    def test_unicode_protected_paths_and_control_bytes_in_messages(self):
        """Unusual Git filenames and raw body delimiters cannot downgrade P to R."""
        with tempfile.TemporaryDirectory() as root:
            def cmd(*args, env=None):
                return subprocess.run(
                    ["git", *args], cwd=root, env=env,
                    capture_output=True, text=True, check=True,
                ).stdout.strip()
            cmd("init", "-q")
            cmd("config", "user.name", "Fixture")
            cmd("config", "user.email", "fixture@example.invalid")
            workflow = Path(root, ".github", "workflows", "déploy.yml")
            workflow.parent.mkdir(parents=True)
            workflow.write_text("name: fixture\n", encoding="utf-8")
            cmd("add", "-A")
            env = {**os.environ,
                   "GIT_AUTHOR_DATE": "2026-10-01T00:00:00+00:00",
                   "GIT_COMMITTER_DATE": "2026-10-01T00:00:00+00:00"}
            subprocess.run(
                ["git", "commit", "-qm",
                 "fix: protect QA_CHECK_FAILED\n\nbody containing \x1e and \x1f raw delimiters"],
                cwd=root, env=env, check=True,
            )
            result = g.census(root, cmd("rev-parse", "HEAD"))
            self.assertEqual(result["population_commits"], 1)
            self.assertEqual(result["classes"], {"P": 1})
            self.assertEqual(result["diff_totals_by_class"]["P"]["added_files"], 1)
            self.assertEqual(result["mechanism_lexical_counts"], {})

    def test_rework_pairs_sort_mixed_timezone_offsets_by_instant(self):
        with tempfile.TemporaryDirectory() as root:
            def cmd(*args, env=None):
                return subprocess.run(
                    ["git", *args], cwd=root, env=env,
                    capture_output=True, text=True, check=True,
                ).stdout.strip()
            cmd("init", "-q")
            cmd("config", "user.name", "Fixture")
            cmd("config", "user.email", "fixture@example.invalid")
            def commit(value, date):
                Path(root, "worker.py").write_text(value, encoding="utf-8")
                cmd("add", "worker.py")
                env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
                subprocess.run(["git", "commit", "-qm", "fix: QA_CHECK_FAILED"],
                               cwd=root, env=env, check=True)
                return cmd("rev-parse", "HEAD")
            older = commit("one\n", "2026-10-02T01:00:00+02:00")
            newer = commit("two\n", "2026-10-02T00:00:00+00:00")
            report = g.census(root, newer)
            self.assertEqual(report["same_code_same_file_72h_candidate_count"], 1)
            pair = report["same_code_same_file_72h_candidates"][0]
            self.assertEqual(pair["older"], older)
            self.assertEqual(pair["newer"], newer)

if __name__=="__main__":
    unittest.main()
