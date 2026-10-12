"""Regression: writers of the immutable D1 ledgers must never request ON CONFLICT DO UPDATE.

wow_d1_training_rows and wow_d1_candidate_artifacts carry BEFORE UPDATE
triggers (wow_reject_immutable_mutation). A PostgREST upsert without
ignore_duplicates turns a re-run over already-persisted identities into an
UPDATE, which the trigger rejects; on 2026-10-10 that blocked the NCAAF
result-form, NCAAB and TENNIS maintenance lanes every run.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from v17 import soccer_openfootball_candidate as soccer
from test_v17_soccer_openfootball_row_order import _season_matches

IMMUTABLE_TABLES = {"wow_d1_training_rows", "wow_d1_candidate_artifacts"}
V17 = Path(__file__).resolve().parent / "v17"


def _immutable_upserts():
    for path in sorted(V17.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "upsert"):
                continue
            receiver = node.func.value
            if (
                isinstance(receiver, ast.Call)
                and isinstance(receiver.func, ast.Attribute)
                and receiver.func.attr == "table"
                and receiver.args
                and isinstance(receiver.args[0], ast.Constant)
                and receiver.args[0].value in IMMUTABLE_TABLES
            ):
                yield path, node


def test_every_immutable_ledger_upsert_ignores_duplicates():
    found = list(_immutable_upserts())
    assert len(found) >= 8
    offenders = []
    for path, node in found:
        kwargs = {kw.arg: kw.value for kw in node.keywords}
        value = kwargs.get("ignore_duplicates")
        if not (isinstance(value, ast.Constant) and value.value is True):
            offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, offenders


class _ImmutableTable:
    def __init__(self, store, name):
        self.store, self.name, self._pending = store, name, None

    def upsert(self, payload, *, on_conflict, ignore_duplicates=False):
        self._pending = (payload if isinstance(payload, list) else [payload], on_conflict, ignore_duplicates)
        return self

    def execute(self):
        rows, on_conflict, ignore = self._pending
        keys = on_conflict.split(",")
        existing = self.store.setdefault(self.name, {})
        for row in rows:
            key = tuple(row[k] for k in keys)
            if key in existing:
                if not ignore:
                    raise RuntimeError("immutable ledger row cannot be updated or deleted")
                continue
            existing[key] = row
        return self


class _ImmutableDB:
    def __init__(self):
        self.store = {}

    def table(self, name):
        return _ImmutableTable(self.store, name)


def test_soccer_lane_rerun_is_idempotent_against_immutable_ledgers(monkeypatch):
    matches = _season_matches()
    monkeypatch.setattr(soccer, "fetch_competition", lambda code: (matches, [{"url": "fixture"}]))
    db = _ImmutableDB()
    first = soccer.train_and_persist_competition(db, competition="EPL", code="en.1", training_code_sha="abcdef123456")
    counts = {name: len(rows) for name, rows in db.store.items()}
    second = soccer.train_and_persist_competition(db, competition="EPL", code="en.1", training_code_sha="abcdef123456")
    assert first["model_artifact_version"] == second["model_artifact_version"]
    assert {name: len(rows) for name, rows in db.store.items()} == counts
    assert counts["wow_d1_candidate_artifacts"] == 1
    assert counts["wow_d1_training_rows"] == first["eligible_rows"]
    assert second["probability_publishable"] is False and second["can_execute"] is False


def test_fake_immutable_db_rejects_update_path():
    db = _ImmutableDB()
    db.table("wow_d1_candidate_artifacts").upsert({"model_artifact_version": "v"}, on_conflict="model_artifact_version").execute()
    with pytest.raises(RuntimeError, match="immutable"):
        db.table("wow_d1_candidate_artifacts").upsert({"model_artifact_version": "v"}, on_conflict="model_artifact_version").execute()
