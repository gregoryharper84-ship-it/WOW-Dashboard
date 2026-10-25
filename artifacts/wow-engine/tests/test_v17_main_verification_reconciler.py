"""Fail-closed release event recovery: no deployment permissions or bypass."""
import json
import os
from pathlib import Path
import re
import subprocess
import textwrap

REPO = Path(__file__).resolve().parents[3]
WATCHDOG = REPO / ".github/workflows/wow-v17-main-verification-reconciler.yml"
RELEASE_GATE = REPO / ".github/workflows/wow-v17-release-production-verification-agent.yml"


def test_watchdog_only_dispatches_missing_main_verify():
    src = WATCHDOG.read_text(encoding="utf-8")
    assert 'cron: "19 * * * *"' in src
    assert "workflow_dispatch:" in src
    assert "actions: write" in src
    assert "deployments: write" not in src
    assert "RENDER_API_KEY" not in src
    assert 'for workflow in wow-verify.yml wow-engine-verify.yml; do' in src
    assert 'actions/workflows/$workflow/dispatches' in src
    assert 'EXACT_MAIN_VERIFY_PRESENT:$workflow:$verdict' in src
    assert 'MISSING_VERIFY_DISPATCHED_FOR_MAIN:$workflow:$main_sha' in src
    assert 'continue' in src
    assert 'head_sha == $sha' in src
    assert '"MISSING"' in src
    assert 'if [ "$verdict" != MISSING ]; then' in src
    assert '"push" or .event == "workflow_dispatch"' in src
    assert "sort_by(.created_at) | reverse | first" in src
    assert '[ "$main_sha" = "$current_sha" ]' in src


def test_watchdog_preserves_execution_and_release_boundaries():
    src = WATCHDOG.read_text(encoding="utf-8")
    assert 'WOW_CAN_EXECUTE: "false"' in src
    assert 'WOW_DRY_RUN_ONLY: "true"' in src
    assert "CHECKS_AND_INDEPENDENT_RELEASE_PROVENANCE_STILL_REQUIRED" in src
    assert "render.com" not in src.lower()


def _mock_gh(tmp_path, state):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json
            import os
            import sys

            state_path = os.environ["MOCK_GH_STATE"]
            with open(state_path, encoding="utf-8") as stream:
                state = json.load(stream)
            args = sys.argv[1:]
            if not args or args[0] != "api":
                raise SystemExit("unexpected gh invocation: " + repr(args))
            method = "GET"
            if "--method" in args:
                method = args[args.index("--method") + 1]
            endpoint = next(arg for arg in args if arg.startswith("repos/"))

            if endpoint.endswith("/branches/main"):
                index = state.get("branch_reads", 0)
                state["branch_reads"] = index + 1
                sha = state["main_shas"][min(index, len(state["main_shas"]) - 1)]
                print(sha)
            elif "/actions/workflows/" in endpoint and "/runs?" in endpoint:
                workflow = endpoint.split("/actions/workflows/", 1)[1].split("/", 1)[0]
                print(json.dumps(state["runs"].get(workflow, [])))
            elif method == "POST" and endpoint.endswith("/dispatches"):
                workflow = endpoint.split("/actions/workflows/", 1)[1].split("/", 1)[0]
                if "ref=main" not in args:
                    raise SystemExit("dispatch ref was not main")
                sha = state["main_shas"][min(state["branch_reads"] - 1, len(state["main_shas"]) - 1)]
                state["dispatches"].append({"workflow": workflow, "sha": sha})
                state["runs"].setdefault(workflow, []).append(
                    {
                        "head_sha": sha,
                        "event": "workflow_dispatch",
                        "status": "queued",
                        "conclusion": None,
                        "created_at": str(len(state["dispatches"])),
                    }
                )
            else:
                raise SystemExit("unexpected gh API endpoint: " + endpoint)

            with open(state_path, "w", encoding="utf-8") as stream:
                json.dump(state, stream)
            """
        ),
        encoding="utf-8",
    )
    gh.chmod(0o755)
    state_path = tmp_path / "gh-state.json"
    if not state_path.exists():
        state_path.write_text(json.dumps(state), encoding="utf-8")
    return bin_dir, state_path


def _run_watchdog(tmp_path, state):
    bin_dir, state_path = _mock_gh(tmp_path, state)
    source = WATCHDOG.read_text(encoding="utf-8")
    step = re.search(
        r"(?ms)^      - name: Reconcile exact-main verification workflows\n"
        r"        shell: bash\n        run: \|\n(?P<script>(?: {10}.*\n)+)",
        source,
    )
    assert step
    script = textwrap.dedent(step.group("script"))
    env = os.environ.copy()
    env.update(
        {
            "GITHUB_REPOSITORY": "gregoryharper84-ship-it/WOW-Dashboard",
            "MOCK_GH_STATE": str(state_path),
            "PATH": f"{bin_dir}:{os.defpath}",
            "WOW_CAN_EXECUTE": "false",
            "WOW_DRY_RUN_ONLY": "true",
        }
    )
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=10,
    )
    return result, json.loads(state_path.read_text(encoding="utf-8"))


def test_watchdog_dispatches_each_missing_exact_main_workflow_once(tmp_path):
    main_sha = "a" * 40
    other_sha = "b" * 40
    state = {
        "main_shas": [main_sha] * 8,
        "runs": {
            "wow-verify.yml": [{"head_sha": other_sha, "event": "push", "status": "completed", "conclusion": "success"}],
            "wow-engine-verify.yml": [{"head_sha": other_sha, "event": "push", "status": "completed", "conclusion": "success"}],
        },
        "dispatches": [],
    }

    result, state = _run_watchdog(tmp_path, state)
    assert result.returncode == 0, result.stderr
    assert state["dispatches"] == [
        {"workflow": "wow-verify.yml", "sha": main_sha},
        {"workflow": "wow-engine-verify.yml", "sha": main_sha},
    ]

    result, state = _run_watchdog(tmp_path, state)
    assert result.returncode == 0, result.stderr
    assert len(state["dispatches"]) == 2
    assert result.stdout.count("EXACT_MAIN_VERIFY_PRESENT:") == 2


def test_watchdog_does_not_retry_present_failure_and_reconciles_other_workflow(tmp_path):
    main_sha = "c" * 40
    state = {
        "main_shas": [main_sha] * 4,
        "runs": {
            "wow-verify.yml": [
                {"head_sha": main_sha, "event": "workflow_dispatch", "status": "completed", "conclusion": "failure"}
            ]
        },
        "dispatches": [],
    }

    result, state = _run_watchdog(tmp_path, state)
    assert result.returncode == 0, result.stderr
    assert state["dispatches"] == [{"workflow": "wow-engine-verify.yml", "sha": main_sha}]
    assert "EXACT_MAIN_VERIFY_PRESENT:wow-verify.yml:completed|failure" in result.stdout


def test_watchdog_holds_dispatch_when_main_is_superseded(tmp_path):
    initial_sha = "d" * 40
    current_sha = "e" * 40
    state = {
        "main_shas": [initial_sha, current_sha],
        "runs": {},
        "dispatches": [],
    }

    result, state = _run_watchdog(tmp_path, state)
    assert result.returncode == 0, result.stderr
    assert "MAIN_SUPERSEDED_HOLD" in result.stdout
    assert state["dispatches"] == []


def test_release_gate_required_workflow_names_match_their_producers():
    source = RELEASE_GATE.read_text(encoding="utf-8")
    required_block = re.search(r"(?ms)^\s*required=\(\n(.*?)^\s*\)", source)
    assert required_block
    required = re.findall(r'"([^"|]+)\|([^"]+)"', required_block.group(1))
    required_by_name = dict(required)
    assert {"wow-verify", "wow-engine-verify"} <= required_by_name.keys()

    for name, path in required:
        workflow_path = REPO / path
        assert workflow_path.is_file(), path
        producer = re.search(
            r"(?m)^name:\s*([^\n]+)$",
            workflow_path.read_text(encoding="utf-8"),
        )
        assert producer and producer.group(1).strip().strip("\"'") == name
