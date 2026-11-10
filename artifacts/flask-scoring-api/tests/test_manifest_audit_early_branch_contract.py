"""Prevent a late local manifest import from breaking fail-closed route returns.

The full integration tests in test_prob_ledger_live_handoff.py also exercise
these branches against a real Flask test client; this AST regression isolates
the Python name-binding contract without requiring external services.
"""
from __future__ import annotations

import ast
from pathlib import Path

APP_FILE = Path(__file__).resolve().parents[1] / "app.py"


def test_gate_engine_manifest_audit_import_precedes_every_local_read():
    tree = ast.parse(APP_FILE.read_text(encoding="utf-8"), filename=str(APP_FILE))
    routes = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "gate_engine_run"
    ]
    assert len(routes) == 1
    route = routes[0]

    bindings = [
        node for node in ast.walk(route)
        if isinstance(node, ast.ImportFrom)
        and node.module == "gate_engine.wow_runtime_manifest"
        and any(alias.name == "MANIFEST_GOVERNANCE_HASH" for alias in node.names)
    ]
    reads = [
        node for node in ast.walk(route)
        if isinstance(node, ast.Name)
        and node.id == "MANIFEST_GOVERNANCE_HASH"
        and isinstance(node.ctx, ast.Load)
    ]

    assert len(bindings) == 1, "manifest import must have exactly one local binding"
    assert reads, "the early audit still needs the manifest hash"
    assert bindings[0].lineno < min(node.lineno for node in reads), (
        "early fail-closed ledger return can otherwise raise UnboundLocalError"
    )
