"""
conftest.py — pytest root for the flask-scoring-api artifact.

Ensures gate_engine (and all other top-level packages in this artifact) are
importable when pytest is invoked from any working directory, including the
monorepo root.  This makes the following equivalent:

    # From artifact root
    pytest tests/test_moneyline_architecture.py

    # From monorepo root
    pytest artifacts/flask-scoring-api/tests/test_moneyline_architecture.py

    # With explicit PYTHONPATH (legacy invocation — still works)
    PYTHONPATH=artifacts/flask-scoring-api pytest ...
"""
import sys
import pathlib

# Insert the artifact root (the directory containing this file) at the front
# of sys.path so that bare `import gate_engine` works regardless of CWD.
_ARTIFACT_ROOT = str(pathlib.Path(__file__).parent.resolve())
if _ARTIFACT_ROOT not in sys.path:
    sys.path.insert(0, _ARTIFACT_ROOT)

# Exclude scripts/ from pytest collection — those files are integration
# test harnesses that connect to a live server (using sys.exit) and are
# not compatible with the unit test runner.
collect_ignore_glob = ["scripts/*", "scripts/**/*"]
