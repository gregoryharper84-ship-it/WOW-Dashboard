"""Configured worker entrypoints must retain the engine root for late imports.

Celery's console launcher temporarily adds cwd while loading -A, then removes
it. Tasks import engine-local modules later, after that temporary scope ends.
Exercise the configured launchers in real isolated workers without a broker
service; the separate Postgres integration covers the actual durable task.
"""
from pathlib import Path
import json
import os
import shlex
import subprocess
import sys
import textwrap

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def _configured_launch(source):
    if source == "render":
        config = yaml.safe_load((ROOT / "render.yaml").read_text())
        worker = next(s for s in config["services"] if s["name"] == "wow-agent-worker")
        return shlex.split(worker["startCommand"])
    config = yaml.load((ROOT / ".github/workflows/wow-engine-verify.yml").read_text(), Loader=yaml.BaseLoader)
    steps = config["jobs"]["agent-runtime-integration"]["steps"]
    script = next(s["run"] for s in steps if "WORKER_PID=$!" in s.get("run", ""))
    command = next(line.strip() for line in script.splitlines() if "agent_runtime.celery_app:celery_app worker" in line)
    return shlex.split(command.partition(" > ")[0])


@pytest.mark.parametrize("source", ["render", "ci"])
def test_configured_worker_keeps_engine_modules_importable_during_task_execution(tmp_path, source):
    # A small local module models the actual delayed ledger import in the
    # durable worker, without connecting to a database in this default suite.
    (tmp_path / "ledger.py").write_text("def get_client():\n    return 'engine-local-ledger'\n")
    (tmp_path / "launch_probe.py").write_text(textwrap.dedent('''
        import json
        import os
        from pathlib import Path
        from celery import Celery, signals
        from celery.worker import state

        app = Celery("worker_launch_probe", broker="memory://")

        @app.task(name="worker_launch_probe.delayed_import")
        def delayed_import():
            result = {"pid": os.getpid()}
            try:
                from ledger import get_client
                result["marker"] = get_client()
            except Exception as exc:
                result["error"] = type(exc).__name__ + ": " + str(exc)
            Path(os.environ["WORKER_LAUNCH_PROBE_RESULT"]).write_text(json.dumps(result))
            state.should_stop = 0
            return result

        @signals.worker_ready.connect
        def enqueue_probe(**_kwargs):
            app.send_task("worker_launch_probe.delayed_import")
    '''))
    result_path = tmp_path / "result.json"
    env = {**os.environ, "WORKER_LAUNCH_PROBE_RESULT": str(result_path)}
    for name in tuple(env):
        if name == "PYTHONPATH" or name.startswith("CELERY_"):
            env.pop(name)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    launch = _configured_launch(source)
    launch[launch.index("-A") + 1] = "launch_probe:app"
    launch += ["--pool=solo", "--concurrency=1", "--without-heartbeat", "--without-gossip", "--without-mingle"]
    completed = subprocess.run(launch, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(result_path.read_text())
    assert result["pid"] != os.getpid()
    assert result.get("marker") == "engine-local-ledger", result
