"""Regress the detailed-evidence installer against the producing snapshot writer."""
from __future__ import annotations

from types import SimpleNamespace

import pick_request_runtime as facade
import pick_request_runtime_core as core
from v17 import detailed_evidence_install as installer


class _Detail:
    def model_dump(self, **_kwargs):
        return {"source": "TEST_ONLY", "can_execute": False}


def _install_isolated(monkeypatch):
    seen = []

    def original(row, normalized, blockers=None):
        seen.append((row.row_key, dict(normalized), list(blockers or [])))
        return "base-id", "base-fingerprint", {
            "source_snapshot_id": "base-id",
            "blockers": list(blockers or []),
            "can_execute": False,
        }

    monkeypatch.setattr(core, "_snapshot_payload", original)
    # The installer creates facade attributes at runtime: register them with
    # monkeypatch first so teardown restores the original global routing.
    monkeypatch.setattr(
        facade, "_snapshot_payload", facade.__dict__.get("_snapshot_payload"), raising=False
    )
    monkeypatch.setattr(core, installer._SNAPSHOT_PATCH_ATTR, False, raising=False)
    monkeypatch.setattr(facade, installer._SNAPSHOT_PATCH_ATTR, False, raising=False)
    installer._patch_snapshot_payload()

    # The production route looks up _snapshot_payload on the core, not facade.
    assert core._snapshot_payload is facade._snapshot_payload
    return seen


def test_detailed_installer_patches_producer_and_forwards_provisional_hold(monkeypatch):
    seen = _install_isolated(monkeypatch)
    row = SimpleNamespace(row_key="held-row")
    result = core._snapshot_payload(row, {"availability_requires_revalidation": True}, ["PLAYER_AVAILABILITY_RECHECK_REQUIRED"])
    assert len(seen) == 1
    assert seen[0][2] == ["PLAYER_AVAILABILITY_RECHECK_REQUIRED"]
    assert result[2]["blockers"] == ["PLAYER_AVAILABILITY_RECHECK_REQUIRED"]
    assert result[2]["can_execute"] is False


def test_detailed_payload_keeps_original_blockers_after_new_fingerprint(monkeypatch):
    seen = _install_isolated(monkeypatch)
    monkeypatch.setattr(installer, "evidence_fingerprint", lambda _: "d" * 64)
    token = installer._detailed_by_row_key.set({"source-row": _Detail()})
    try:
        _id, fingerprint, persisted = core._snapshot_payload(
            SimpleNamespace(row_key="source-row"),
            {"availability_status": "UNVERIFIED"},
            ["PLAYER_AVAILABILITY_UNVERIFIED", "SECONDARY_HOLD"],
        )
    finally:
        installer._detailed_by_row_key.reset(token)
    assert len(seen) == 1
    assert fingerprint != "base-fingerprint"
    assert persisted["source_snapshot_id"] != "base-id"
    assert persisted["detailed_evidence_fingerprint"] == "d" * 64
    assert persisted["detailed_evidence"]["source"] == "TEST_ONLY"
    assert persisted["blockers"] == ["PLAYER_AVAILABILITY_UNVERIFIED", "SECONDARY_HOLD"]
    assert persisted["can_execute"] is False
