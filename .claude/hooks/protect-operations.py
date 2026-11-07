#!/usr/bin/env python3
"""Claude Code PreToolUse guard. Best-effort command hygiene; GitHub protections remain mandatory.

Exit 0: continue normal permission evaluation. Exit 2: deny tool call.
No shell execution is performed by this hook.
"""
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

PROTECTED = {"main", "master"}
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
SENSITIVE_VAR = re.compile(r"\$\{?(?:[A-Za-z_][A-Za-z0-9_]*?)?(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PRIVATE|CREDENTIAL|AUTH)(?:_[A-Za-z0-9_]*)?\}?(?![A-Za-z0-9_])", re.I)
UNSAFE_SHELL = re.compile(r"`|\$\(|<<")
SECRET_FILE = re.compile(r"(?:^|[/\\])\.env(?:\.(?!example(?:$|[\s/\\]))[^/\\\s]*)?$|\.pem$|\.key$", re.I)
PROTECTED_API = re.compile(r"(?:/merges(?:$|[/?])|/pulls/\d+/merge(?:$|[/?])|/git/refs/(?:heads/)?(?:main|master)(?:$|[/?])|/branches/(?:main|master)/protection(?:$|[/?]))", re.I)
MUTATION_OPTIONS = ("-f", "--field", "-F", "--raw-field", "--input")


def is_secret_path(value):
    value = value.strip("'\";()<>|& ")
    if value.endswith(".env.example") or Path(value).name == ".env.example":
        return False
    return bool(SECRET_FILE.search(value)) or bool(re.search(r"(?:^|[/\\])\.env(?:\.[^/\\\s]+)?(?=$|[\s])", value))


def current_branch(cwd):
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"], cwd=cwd,
            capture_output=True, text=True, timeout=3, check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def reject(reason):
    print("WOW V17 guard: " + reason, file=sys.stderr)
    raise SystemExit(2)


def git_subcommand(tokens, pos):
    """Return position of git subcommand after global options, or None."""
    i = pos + 1
    takes_arg = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
    while i < len(tokens):
        tok = tokens[i]
        if tok in takes_arg:
            i += 2
        elif any(tok.startswith(o + "=") for o in takes_arg):
            i += 1
        elif tok.startswith("-"):
            i += 1
        else:
            return i
    return None


def is_protected_ref(value):
    value = value.lstrip("+").strip()
    if value.startswith(":"):
        value = value[1:]
    return value in PROTECTED or value in {"refs/heads/main", "refs/heads/master"} or value.startswith("rescue/") or value.startswith("refs/heads/rescue/")


def push_violation(args, branch):
    if branch in PROTECTED:
        return "git push from protected working branch"
    for a in args:
        if a in {"--force", "--force-with-lease", "--force-if-includes", "--mirror", "--all"} or a.startswith(("--force=", "--force-with-lease=")):
            return "force/mirror/all push blocked"
        if re.fullmatch(r"-[A-Za-z]*f[A-Za-z]*", a) and a.startswith("-"):
            return "short-option force push blocked"
        if a.startswith("+"):
            return "force refspec blocked"
    deletion = any(x in {"--delete", "-d"} for x in args)
    positionals = [a for a in args if not a.startswith("-")]
    if any(is_protected_ref(a) for a in positionals):
        return "protected destination branch push blocked"
    for a in args:
        if ":" in a and not a.startswith("--"):
            src, dst = a.rsplit(":", 1)
            if is_protected_ref(dst):
                return "protected destination refspec blocked"
            if src == "" and is_protected_ref(dst):
                return "protected remote ref deletion blocked"
        if deletion and is_protected_ref(a):
            return "protected remote ref deletion blocked"
    if any(a.startswith(":rescue/") or a.startswith(":refs/heads/rescue/") for a in args):
        return "rescue ref deletion blocked"
    return None


def separate_shell_parentheses(command):
    """Expose unquoted subshell boundaries without altering quoted refnames."""
    output = []
    quote = None
    escaped = False
    for char in command:
        if escaped:
            output.append(char)
            escaped = False
        elif char == "\\" and quote != "'":
            output.append(char)
            escaped = True
        elif char in ("'", '"'):
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            output.append(char)
        elif char in "()" and quote is None:
            # A semicolon boundary keeps git/gh parsing from seeing 'main)'.
            output.append(" ; ")
        else:
            output.append(char)
    return "".join(output)


def command_violation(command, branch, depth=0):
    if depth > 4:
        return "nested shell depth unsupported"
    if not isinstance(command, str) or "\x00" in command:
        return "invalid shell command"
    # Command substitutions/backticks defeat token-level inspection. Fail closed rather than guess.
    if UNSAFE_SHELL.search(command):
        return "shell substitution/here-document cannot be safely inspected"
    if SENSITIVE_VAR.search(command):
        return "secret-like variable expansion blocked"
    try:
        parser = shlex.shlex(separate_shell_parentheses(command), posix=True, punctuation_chars=";&|")
        parser.whitespace_split = True
        parser.commenters = ""
        tokens = list(parser)
    except ValueError:
        return "unparseable shell command"
    if not tokens:
        return None
    delimiters = {";", "&&", "||", "|", "&"}
    def command_end(start):
        return next((j for j in range(start, len(tokens)) if tokens[j] in delimiters), len(tokens))
    for idx, tok in enumerate(tokens):
        clean = tok.strip(";&|()")
        # Only commands, not ordinary arguments named "env"/"eval", are
        # environment-dumping or arbitrary-evaluation operations.
        segment_start = max((j + 1 for j in range(idx) if tokens[j] in delimiters), default=0)
        prefix = tokens[segment_start:idx]
        command_position = not prefix or all(
            part in {"sudo", "command", "builtin", "exec", "nohup"} for part in prefix
        )
        if command_position and (
            clean in {"eval", "printenv", "env"}
            or (
                clean in {"export", "set"} and idx + 1 < len(tokens)
                and tokens[idx + 1] in {"-p", "+o"}
            )
        ):
            return "unbounded environment or evaluation command blocked"
        if clean in {"cat", "head", "tail", "sed", "awk", "grep", "less", "more", "nl", "cp", "dd", "base64"}:
            # Path checks below also catch redirects, piping and options.
            pass
        if is_secret_path(clean):
            return "secret-bearing file access blocked"
        if clean in {"bash", "sh", "zsh"}:
            # Inspect quoted shell -c payloads, preserving a failsafe for badly specified scripts.
            if idx + 1 < len(tokens):
                for j in range(idx + 1, min(idx + 4, len(tokens))):
                    if tokens[j] in {"-c", "-lc", "-ec", "-xec", "-euc"}:
                        if j + 1 >= len(tokens):
                            return "shell -c has no payload"
                        nested = command_violation(tokens[j + 1], branch, depth + 1)
                        if nested:
                            return nested
        if clean == "git":
            sub = git_subcommand(tokens, idx)
            if sub is None:
                continue
            op = tokens[sub].strip(";&|()")
            args = tokens[sub + 1:command_end(sub)]
            if op == "push":
                reason = push_violation(args, branch)
                if reason:
                    return reason
            if op in {"commit", "cherry-pick", "rebase", "merge", "reset"} and branch in PROTECTED:
                return "git mutation on protected working branch blocked"
            if op == "branch" and any(a in {"-d", "-D", "--delete"} for a in args):
                if any(is_protected_ref(a) for a in args):
                    return "protected local branch deletion blocked"
            if op == "update-ref" and any(is_protected_ref(a) for a in args):
                return "direct protected ref update blocked"
        if clean == "gh":
            gh_args = tokens[idx + 1:command_end(idx)]
            if not gh_args:
                continue
            if gh_args[0] == "pr" and len(gh_args) > 1:
                if gh_args[1] == "merge":
                    return "gh pr merge blocked; use authorized release"
                if gh_args[1] == "review" and any(flag in gh_args[2:] for flag in ("--approve", "-a")):
                    return "self-approval blocked"
            if gh_args[0] == "api":
                options = gh_args[1:]
                has_write = any(
                    a in MUTATION_OPTIONS
                    or a.startswith(("-f", "-F", "--field=", "--raw-field=", "--input="))
                    for a in options
                )
                for j, option in enumerate(options):
                    if option in {"-X", "--method"} and j + 1 < len(options):
                        has_write |= options[j + 1].upper() in {"POST", "PATCH", "PUT", "DELETE"}
                    if option.startswith("-X") and len(option) > 2:
                        has_write |= option[2:].upper() in {"POST", "PATCH", "PUT", "DELETE"}
                    if option.startswith("--method="):
                        has_write |= option.split("=", 1)[1].upper() in {"POST", "PATCH", "PUT", "DELETE"}
                if has_write and any(PROTECTED_API.search(a) for a in options):
                    return "mutating protected gh api endpoint blocked"
    return None


def evaluate(payload, branch=None):
    if not isinstance(payload, dict):
        return "invalid hook input"
    tool = payload.get("tool_name", "")
    args = payload.get("tool_input") or {}
    if not isinstance(args, dict):
        return "invalid tool payload"
    if tool in {"Write", "Edit", "MultiEdit"}:
        paths = [args.get("file_path", ""), args.get("path", "")]
        return "secret-bearing file write blocked" if any(is_secret_path(str(p)) for p in paths if p) else None
    if tool != "Bash":
        return None
    command = args.get("command", "")
    cwd = payload.get("cwd") or os.getcwd()
    return command_violation(command, branch if branch is not None else current_branch(cwd))


def main():
    try:
        payload = json.load(sys.stdin)
    except (OSError, ValueError):
        reject("invalid hook JSON")
    try:
        reason = evaluate(payload)
    except Exception:
        reject("internal guard error; failing closed")
    if reason:
        reject(reason)


if __name__ == "__main__":
    main()
