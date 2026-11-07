#!/usr/bin/env python3
"""Offline regression suite for the PreToolUse guard. No GitHub/network writes."""
import importlib.util
import io
import json
from unittest import mock
import subprocess
import sys
import unittest
from pathlib import Path

HOOK_PATH = Path(__file__).with_name("protect-operations.py")
spec = importlib.util.spec_from_file_location("wow_guard", HOOK_PATH)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

BLOCKED = [
    "git push origin main",
    "(git push origin main)",
    "git push origin rescue/replit-emergency-20260820-1221",
    "git push origin master",
    "git push origin feat:main",
    "git push origin feat:master",
    "git push origin feat:refs/heads/main",
    "git push origin feat:refs/heads/master",
    "git push origin HEAD:main",
    "git push origin HEAD:refs/heads/main",
    "git push origin :main",
    "git push origin :refs/heads/main",
    "git push origin :rescue/replit-emergency-20260820-1221",
    "git push --delete origin main",
    "git push --delete origin master",
    "git push --delete origin rescue/foo",
    "git push --all origin",
    "git push --mirror origin",
    "git push -f origin feature/x",
    "git push -fu origin feature/x",
    "git push -uf origin feature/x",
    "git push --force origin feature/x",
    "git push --force-with-lease origin feature/x",
    "git push --force-if-includes origin feature/x",
    "git push origin +feat",
    "git -C . push --force origin feature/x",
    "git --git-dir=.git push origin feat:main",
    "sudo env git push origin feat:main",
    "git push origin feature/x && git push origin main",
    "echo ok; git push origin main",
    "git push origin feature/x || gh pr merge 21",
    "bash -c 'git push origin feat:main'",
    'bash -lc "git push origin main"',
    "sh -c 'git push -f origin feature/x'",
    "gh pr merge 123",
    "gh pr merge 123 --squash",
    "gh pr review 111 --approve",
    "gh pr review 111 -a",
    "echo gh && git push origin main",
    "gh; git push origin main",
    "gh pr review --approve 111",
    "gh api -X POST repos/org/repo/pulls/1/merge",
    "gh api -XPOST repos/org/repo/pulls/1/merge",
    "gh api -XDELETE repos/org/repo/branches/main/protection",
    "gh api -fsha=abcdef repos/org/repo/pulls/1/merge",
    "gh api -Fkey=xyz repos/org/repo/merges",
    "gh api --method PUT repos/org/repo/branches/main/protection",
    "gh api repos/org/repo/git/refs/heads/main -f sha=abcdef",
    "gh api repos/org/repo/branches/master/protection --raw-field=x",
    "gh api repos/org/repo/merges --method=POST",
    "git branch -D main",
    "git branch --delete rescue/protected",
    "git update-ref refs/heads/main deadbeef",
    "cat .env",
    "cat .env.production",
    "cat ./secrets/.env",
    "head ./private.pem",
    "tail /tmp/credentials.key",
    "sed -n '1,20p' .env.test",
    "grep PASS .env",
    "cat .env && git status",
    "printenv",
    "env",
    "env | grep KEY",
    "export -p",
    "set +o",
    "echo $OPENAI_API_KEY",
    "echo $TOKEN",
    "echo ${SECRET}",
    "echo $API_KEY_2",
    "echo ${SERVICE_SECRET}",
    "printf '%s' $ACCESS_TOKEN",
    'bash -c "cat .env"',
    "git push origin feature/x; cat ./private.pem",
    "eval 'git push origin main'",
    'bash -c "echo unterminated',
    'git push origin feature/x && echo $(cat .env)',
    'git push origin feature/x && echo `cat .env`',
    'cat <<EOF',
]

ALLOWED = [
    "git status",
    "git branch --show-current",
    "git log -1 --oneline",
    "git diff --stat",
    "git show --stat HEAD",
    "git push -u origin feature/v17-hooks",
    "git push origin feature/v17-hooks",
    "git push origin 'main)'",
    "git push origin feat:feature/v17-safe",
    "git push origin :feature/obsolete",
    "git push --delete origin feature/obsolete",
    "git -C . push origin feat:feature/v17-safe",
    "git switch -c feature/foo",
    "git branch -D feature/obsolete",
    "git add CLAUDE.md",
    "git commit -m 'docs: updated Claude'",
    "git rebase origin/main",
    "gh pr create --draft --title 'docs'",
    "gh pr view 123 --json state",
    "gh pr checks 123",
    "gh api repos/org/repo/branches/main/protection",
    "gh api repos/org/repo/git/ref/heads/main",
    "gh api repos/org/repo/issues/2/comments",
    "gh workflow run unit-tests.yml",
    "cat .env.example",
    "cat README.md",
    "head -n 10 CLAUDE.md",
    "python3 -I -m unittest discover",
    "python3 -m json.tool .claude/settings.json",
    "echo $HOME",
    "echo $AUTHOR",
    "python -m venv env",
    "pytest -k env",
    "git checkout -b eval",
    "echo project-ready",
    "pwd",
    "ls -la",
    "date -u",
    "rg 'MODEL_UNAVAILABLE' artifacts/wow-engine",
    "git status && git diff --stat",
    "printf 'yes'",
    "git push origin feature/v17-safe && echo main",
    "echo 'git push origin main'",
]


class GuardTests(unittest.TestCase):
    def test_denied_bash(self):
        for command in BLOCKED:
            with self.subTest(command=command):
                payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": "."}
                self.assertIsNotNone(guard.evaluate(payload, branch="feature/test"))

    def test_allowed_bash(self):
        for command in ALLOWED:
            with self.subTest(command=command):
                payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": "."}
                self.assertIsNone(guard.evaluate(payload, branch="feature/test"))

    def test_protected_branch_writes(self):
        for branch in ("main", "master"):
            for command in ("git commit -m x", "git cherry-pick abc", "git rebase HEAD~1", "git merge foo", "git reset --hard HEAD~1", "git push origin feature/foo"):
                with self.subTest(branch=branch, command=command):
                    self.assertIsNotNone(guard.evaluate({"tool_name": "Bash", "tool_input": {"command": command}}, branch=branch))

    def test_file_tools(self):
        for tool in ("Write", "Edit", "MultiEdit"):
            for path in (".env", "./.env.prod", "config/secrets.pem", "/tmp/private.key"):
                with self.subTest(tool=tool, path=path):
                    self.assertIsNotNone(guard.evaluate({"tool_name": tool, "tool_input": {"file_path": path}}))
            for path in (".env.example", "CLAUDE.md", "README.md"):
                with self.subTest(tool=tool, path=path):
                    self.assertIsNone(guard.evaluate({"tool_name": tool, "tool_input": {"file_path": path}}))

    def test_input_fail_closed(self):
        for payload in (None, [1, 2], {"tool_name": "Bash", "tool_input": "bad"}):
            with self.subTest(payload=payload):
                self.assertIsNotNone(guard.evaluate(payload, branch="feature/test"))
        proc = subprocess.run([sys.executable, "-I", str(HOOK_PATH)], input="garbage", capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("invalid hook JSON", proc.stderr)

    def test_internal_evaluation_failure_denies_with_exit_two(self):
        with mock.patch.object(guard, "evaluate", side_effect=RuntimeError("synthetic failure")):
            with mock.patch.object(sys, "stdin", io.StringIO("{}")):
                with self.assertRaises(SystemExit) as raised:
                    guard.main()
        self.assertEqual(raised.exception.code, 2)

    def test_exit_code_blocks(self):
        p = subprocess.run([sys.executable, "-I", str(HOOK_PATH)], input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "gh pr merge 12"}}), capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)
        p = subprocess.run([sys.executable, "-I", str(HOOK_PATH)], input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "git status"}}), capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
