from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"PATCH_TARGET_COUNT:{path}:{count}:{old[:120]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# 1. Preserve response_mode through the outer detailed-evidence wrapper.
detailed = "artifacts/wow-engine/v17/detailed_evidence_install.py"
replace_once(detailed, "from typing import Any, Optional\n", "from typing import Any, Literal, Optional\n")
replace_once(
    detailed,
    'class DetailedPickRequestBatch(BaseModel):\n    model_config = ConfigDict(extra="forbid")\n    request_id: Optional[str] = None\n    rows: list[DetailedPickRequestRow] = Field(min_length=1, max_length=50)\n',
    'class DetailedPickRequestBatch(BaseModel):\n    model_config = ConfigDict(extra="forbid")\n    request_id: Optional[str] = None\n    response_mode: Literal["FULL", "COMPACT"] = "FULL"\n    rows: list[DetailedPickRequestRow] = Field(min_length=1, max_length=50)\n',
)
replace_once(
    detailed,
    '        base_batch = pick_runtime.PickRequestBatch(\n            request_id=batch.request_id,\n            rows=base_rows,\n        )\n',
    '        base_batch = pick_runtime.PickRequestBatch(\n            request_id=batch.request_id,\n            response_mode=batch.response_mode,\n            rows=base_rows,\n        )\n',
)
replace_once(
    detailed,
    '        if isinstance(result, dict) and isinstance(result.get("rows"), list):\n            for outcome in result["rows"]:\n',
    '        if isinstance(result, dict):\n            result["response_mode"] = batch.response_mode\n\n        if batch.response_mode == "FULL" and isinstance(result, dict) and isinstance(result.get("rows"), list):\n            for outcome in result["rows"]:\n',
)

# 2. A flattened COMPACT receipt is still a valid governed model package.
top10 = "artifacts/wow-engine/v17/top10_model_reconciliation.py"
replace_once(
    top10,
    'def has_valid_model_package(outcome: dict[str, Any]) -> bool:\n    if outcome.get("model_evaluated") is not True:\n        return False\n    for package in _candidate_probability_dicts(outcome):\n',
    'def has_valid_model_package(outcome: dict[str, Any]) -> bool:\n    if outcome.get("model_evaluated") is not True:\n        return False\n    compact_lower = outcome.get("calibrated_probability_lower_bound")\n    if compact_lower is None:\n        compact_lower = outcome.get("calibrated_lower_bound")\n    if _finite_probability(outcome.get("calibrated_probability")) and _finite_probability(compact_lower):\n        return True\n    for package in _candidate_probability_dicts(outcome):\n',
)

# 3. Harden the COMPACT serializer. Large evidence stays server-side.
core = "artifacts/wow-engine/pick_request_runtime_core.py"
replace_once(
    core,
    '_PICK_REQUEST_COMPACT_DETAIL_KEYS = frozenset({"result", "detail", "acquisition", "specialist_utilization_audit"})\n',
    '_PICK_REQUEST_COMPACT_DETAIL_KEYS = frozenset({\n    "result", "detail", "acquisition", "specialist_utilization_audit",\n    "evidence", "acquisition_evidence", "box_score_log", "game_log",\n    "model_evidence", "failure_path_evidence", "directional_probability_assessments",\n    "v17_numerical_engine", "objective_lanes", "backend_traversal",\n    "detailed_evidence", "artifact_metadata", "training_metadata", "source_timestamps",\n})\n',
)
replace_once(
    core,
    '        for key in ("blocker_code", "failure_class", "blocker", "sport", "stat_type", "controlling_specialist"):\n',
    '        for key in (\n            "blocker_code", "failure_class", "blocker", "primary_blocker", "blockers",\n            "event_id", "event_start_time", "sport", "league", "player", "stat_type",\n            "line", "direction", "controlling_specialist",\n        ):\n',
)
replace_once(
    core,
    '        for source, mapping in ((prediction, {"raw_model_probability":"model_probability","calibrated_probability":"calibrated_probability","calibrated_probability_lower_bound":"calibrated_probability_lower_bound","calibration_status":"calibration_status","model_version":"model_version"}),(qualification, {"confidence_tier":"confidence_tier","rank_eligible":"rank_eligible","model_supported":"model_supported","terminal_label":"terminal_label"})):\n',
    '        for source, mapping in ((prediction, {"prediction_id":"prediction_id","raw_model_probability":"model_probability","calibrated_probability":"calibrated_probability","calibrated_probability_lower_bound":"calibrated_probability_lower_bound","calibrated_probability_upper_bound":"calibrated_probability_upper_bound","calibrated_upper_bound":"calibrated_probability_upper_bound","calibration_status":"calibration_status","model_version":"model_version"}),(qualification, {"confidence_tier":"confidence_tier","rank_eligible":"rank_eligible","model_supported":"model_supported","terminal_label":"terminal_label"})):\n',
)
replace_once(
    core,
    '    compact["detail_available"] = any(k in outcome for k in _PICK_REQUEST_COMPACT_DETAIL_KEYS)\n    compact["can_execute"] = False\n    return compact\n',
    '    detail_available = any(k in outcome for k in _PICK_REQUEST_COMPACT_DETAIL_KEYS)\n    compact["detail_available"] = detail_available\n    compact["detail_ref"] = {\n        "prediction_id": compact.get("prediction_id"),\n        "row_key": compact.get("row_key"),\n        "detail_available": detail_available,\n    }\n    compact["can_execute"] = False\n    return compact\n',
)

# 4. Add a side-effect-free canonical sporting identity helper.
identity_path = ROOT / "artifacts/wow-engine/v17/prop_canonical_identity.py"
identity_path.write_text('''"""Canonical sporting identity for V17 prop source instances.\n\nSource snapshot/provider identifiers are provenance, not sporting identity.\nThis helper never changes model math, calibration, terminal semantics, or execution.\n"""\nfrom __future__ import annotations\n\nfrom datetime import datetime, timezone\nfrom decimal import Decimal, InvalidOperation\nfrom typing import Any, Iterable\n\n_SPORT_ALIASES = {"CFB": "NCAAF", "COLLEGE_FOOTBALL": "NCAAF", "NCAA_FOOTBALL": "NCAAF"}\n\ndef _text(value: Any) -> str:\n    return "_".join(str(value or "").strip().upper().replace("-", " ").split())\n\ndef _player(value: Any) -> str:\n    return " ".join(str(value or "").split()).casefold()\n\ndef _line(value: Any) -> str | None:\n    try:\n        return format(Decimal(str(value)).normalize(), "f")\n    except (InvalidOperation, TypeError, ValueError):\n        return None\n\ndef _time(value: Any) -> str:\n    raw = str(value or "").strip()\n    if not raw:\n        return ""\n    try:\n        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))\n    except (TypeError, ValueError):\n        return raw\n    if parsed.utcoffset() is None:\n        return raw\n    return parsed.astimezone(timezone.utc).isoformat()\n\ndef canonical_sporting_key(row: dict[str, Any]) -> tuple[str, ...] | None:\n    sport = _SPORT_ALIASES.get(_text(row.get("sport")), _text(row.get("sport")))\n    event_id = str(row.get("event_id") or row.get("official_event_id") or "").strip()\n    event_start = _time(row.get("event_start_time") or row.get("event_start_time_utc"))\n    player = _player(row.get("player") or row.get("participant"))\n    stat_type = _text(row.get("stat_type") or row.get("market_stat"))\n    line = _line(row.get("line"))\n    if not sport or not event_id or not event_start or not player or not stat_type or line is None:\n        return None\n    direction = _text(row.get("direction"))\n    period = _text(row.get("period"))\n    settlement = _text(row.get("settlement_identity") or row.get("settlement_basis"))\n    return sport, event_id, event_start, player, stat_type, line, direction, period, settlement\n\ndef _snapshot_ids(row: dict[str, Any]) -> list[str]:\n    values: list[str] = []\n    for value in row.get("source_snapshot_ids") or []:\n        text = str(value or "").strip()\n        if text and text not in values:\n            values.append(text)\n    single = str(row.get("source_snapshot_id") or "").strip()\n    if single and single not in values:\n        values.append(single)\n    return values\n\ndef _captured_at(row: dict[str, Any]) -> datetime:\n    raw = str(row.get("captured_at") or "").strip()\n    try:\n        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))\n        if parsed.utcoffset() is not None:\n            return parsed.astimezone(timezone.utc)\n    except (TypeError, ValueError):\n        pass\n    return datetime.min.replace(tzinfo=timezone.utc)\n\ndef canonicalize_prop_manifest(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:\n    selected: list[dict[str, Any]] = []\n    index_by_key: dict[tuple[str, ...], int] = {}\n    for raw in rows:\n        row = dict(raw)\n        key = canonical_sporting_key(row)\n        ids = _snapshot_ids(row)\n        if key is None:\n            row["source_snapshot_ids"] = ids\n            row["source_instance_count"] = max(len(ids), 1)\n            row["canonical_sporting_key"] = None\n            selected.append(row)\n            continue\n        index = index_by_key.get(key)\n        if index is None:\n            row["source_snapshot_ids"] = ids\n            row["source_instance_count"] = max(len(ids), 1)\n            row["canonical_sporting_key"] = "|".join(key)\n            index_by_key[key] = len(selected)\n            selected.append(row)\n            continue\n        current = selected[index]\n        merged_ids = _snapshot_ids(current)\n        for snapshot_id in ids:\n            if snapshot_id not in merged_ids:\n                merged_ids.append(snapshot_id)\n        if _captured_at(row) > _captured_at(current):\n            replacement = row\n            replacement["canonical_sporting_key"] = current.get("canonical_sporting_key")\n            selected[index] = replacement\n            current = replacement\n        current["source_snapshot_ids"] = merged_ids\n        current["source_instance_count"] = max(len(merged_ids), 1)\n    return selected\n\ndef canonical_source_snapshot_ids(rows: Iterable[dict[str, Any]]) -> set[str]:\n    ids: set[str] = set()\n    for row in rows:\n        ids.update(_snapshot_ids(row))\n    return ids\n\n__all__ = ["canonical_sporting_key", "canonicalize_prop_manifest", "canonical_source_snapshot_ids"]\n''', encoding="utf-8")

# 5. Canonicalize Daily snapshot rows before max_props/scoring.
daily = "artifacts/wow-engine/v17/daily_snapshot_runtime.py"
replace_once(
    daily,
    "from v17.prop_forward_cohort_route import install_prop_forward_cohort_route\n",
    "from v17.prop_forward_cohort_route import install_prop_forward_cohort_route\nfrom v17.prop_canonical_identity import canonical_source_snapshot_ids, canonicalize_prop_manifest\n",
)
replace_once(
    daily,
    '    selected = "source_snapshot_id,event_id,event_start_time,sport,player,stat_type,line,hydration_status,blockers"\n',
    '    selected = "source_snapshot_id,captured_at,event_id,event_start_time,sport,player,stat_type,line,hydration_status,blockers"\n',
)
replace_once(
    daily,
    "        offset += page_size\n    return manifest\n\n\ndef _prop_rows",
    "        offset += page_size\n    return canonicalize_prop_manifest(manifest)\n\n\ndef _prop_rows",
)
replace_once(
    daily,
    '    canonical_ids = {str(row.get("source_snapshot_id")) for row in canonical_manifest if row.get("source_snapshot_id")}\n',
    '    canonical_ids = canonical_source_snapshot_ids(canonical_manifest)\n',
)
replace_once(
    daily,
    '    canonical_count = len(canonical_manifest or []) if lane == "PROPS" else len(lane_rows)\n',
    '    canonical_count = len(canonical_manifest or []) if lane == "PROPS" else len(lane_rows)\n    source_instance_count = (\n        sum(int(row.get("source_instance_count") or 1) for row in (canonical_manifest or []))\n        if lane == "PROPS"\n        else canonical_count\n    )\n',
)
replace_once(
    daily,
    '        "discovered_count": canonical_count,\n        "canonicalized_count": canonical_count,\n',
    '        "discovered_count": source_instance_count,\n        "canonicalized_count": canonical_count,\n        "duplicate_source_instance_count": max(source_instance_count - canonical_count, 0),\n',
)

# Regression tests.
(ROOT / "artifacts/wow-engine/tests/test_v17_compact_outer_wrapper.py").write_text('''from __future__ import annotations\n\nimport pytest\nfrom fastapi import FastAPI, Header\nfrom fastapi.testclient import TestClient\n\nimport pick_request_runtime as pick_runtime\nimport v17.detailed_evidence_install as subject\n\n@pytest.mark.parametrize("sport", ["MLB", "NFL", "WNBA", "NCAAF"])\ndef test_detailed_evidence_wrapper_preserves_compact_mode(monkeypatch, sport):\n    app = FastAPI()\n    captured = {}\n    @app.post("/score-pick-request")\n    def base_route(batch: pick_runtime.PickRequestBatch, x_wow_model_identity: str | None = Header(default=None, alias="X-WOW-Model-Identity")):\n        captured["response_mode"] = batch.response_mode\n        return {"ok": True, "response_mode": batch.response_mode, "rows": [{"row_key": batch.rows[0].row_key, "terminal_status": "HELD", "code": "TEST_TYPED_BLOCKER", "model_evaluated": False, "probability_publishable": False, "rank_eligible": False, "can_execute": False}], "can_execute": False}\n    monkeypatch.setattr(subject, "_patch_model_features", lambda _market: None)\n    monkeypatch.setattr(subject, "_patch_snapshot_payload", lambda: None)\n    monkeypatch.setattr(subject, "_patch_team_event_canonicalizer", lambda: None)\n    subject.install_v17_detailed_evidence(app, auth_dependency=lambda: None, market_api=object())\n    response = TestClient(app).post("/score-pick-request", json={"request_id": f"compact-{sport.lower()}", "response_mode": "COMPACT", "rows": [{"row_key": "row-1", "event_id": f"{sport}-event-1", "event_start_time": "2099-09-20T20:00:00Z", "sport": sport, "player": "Test Player", "stat_type": "TEST_STAT", "line": 1.5, "direction": "MORE"}]})\n    assert response.status_code == 200, response.text\n    payload = response.json()\n    assert captured["response_mode"] == "COMPACT"\n    assert payload["response_mode"] == "COMPACT"\n    assert payload["can_execute"] is False\n''', encoding="utf-8")

(ROOT / "artifacts/wow-engine/tests/test_v17_compact_receipt_contract.py").write_text('''from pick_request_runtime_core import _compact_pick_outcome\nfrom v17.top10_model_reconciliation import has_valid_model_package\n\ndef test_compact_receipt_is_bounded_and_preserves_probability_package():\n    source = {"row_key": "r1", "terminal_status": "COMPLETED", "code": "FINAL_APPROVED", "model_evaluated": True, "detail": {"event_id": "evt-1", "event_start_time": "2099-09-20T20:00:00Z", "sport": "MLB", "league": "MLB", "player": "Pitcher One", "stat_type": "PITCHER_STRIKEOUTS", "line": 6.5, "direction": "MORE", "controlling_specialist": "wow.mlb-pitcher-failure-path-expert", "blockers": []}, "result": {"prediction": {"prediction_id": "pred-1", "raw_model_probability": 0.72, "calibrated_probability": 0.69, "calibrated_probability_lower_bound": 0.63, "calibrated_probability_upper_bound": 0.75, "calibration_status": "PASS", "model_version": "v1"}, "probability_qualification": {"rank_eligible": True, "model_supported": True, "confidence_tier": "HIGH", "terminal_label": "MODEL_QUALIFIED"}}, "detailed_evidence": {"huge": "x" * 10000}, "model_evidence": {"huge": "x" * 10000}, "failure_path_evidence": {"huge": "x" * 10000}, "directional_probability_assessments": [{"x": "y" * 1000}], "v17_numerical_engine": {"huge": "x" * 10000}, "objective_lanes": {"huge": "x" * 10000}, "backend_traversal": {"huge": "x" * 10000}, "specialist_utilization_audit": {"huge": "x" * 10000}, "can_execute": False}\n    out = _compact_pick_outcome(source)\n    assert out["prediction_id"] == "pred-1"\n    assert out["model_probability"] == 0.72\n    assert out["calibrated_probability"] == 0.69\n    assert out["calibrated_probability_lower_bound"] == 0.63\n    assert out["calibrated_probability_upper_bound"] == 0.75\n    assert out["rank_eligible"] is True\n    assert out["detail_ref"]["prediction_id"] == "pred-1"\n    assert has_valid_model_package(out) is True\n    for forbidden in ("result", "detail", "detailed_evidence", "model_evidence", "failure_path_evidence", "directional_probability_assessments", "v17_numerical_engine", "objective_lanes", "backend_traversal", "specialist_utilization_audit"):\n        assert forbidden not in out\n    assert out["can_execute"] is False\n''', encoding="utf-8")

(ROOT / "artifacts/wow-engine/tests/test_v17_prop_canonical_identity.py").write_text('''from v17.prop_canonical_identity import canonical_sporting_key, canonicalize_prop_manifest\n\ndef _row(snapshot_id, *, line=6.5, direction=None, captured="2099-09-20T10:00:00Z"):\n    row = {"source_snapshot_id": snapshot_id, "captured_at": captured, "event_id": "evt-1", "event_start_time": "2099-09-20T20:00:00Z", "sport": "MLB", "player": "Pitcher One", "stat_type": "PITCHER_STRIKEOUTS", "line": line}\n    if direction is not None: row["direction"] = direction\n    return row\n\ndef test_snapshot_id_is_provenance_not_sporting_identity():\n    rows = [_row(f"snap-{i}", captured=f"2099-09-20T1{i}:00:00Z") for i in range(4)]\n    out = canonicalize_prop_manifest(rows)\n    assert len(out) == 1\n    assert out[0]["source_snapshot_ids"] == ["snap-0", "snap-1", "snap-2", "snap-3"]\n    assert out[0]["source_snapshot_id"] == "snap-3"\n    assert out[0]["source_instance_count"] == 4\n\ndef test_materially_different_lines_and_directions_remain_distinct():\n    assert canonical_sporting_key(_row("a", line=6.5)) != canonical_sporting_key(_row("b", line=7.5))\n    assert canonical_sporting_key(_row("a", direction="MORE")) != canonical_sporting_key(_row("b", direction="LESS"))\n    assert len(canonicalize_prop_manifest([_row("a", line=6.5), _row("b", line=7.5)])) == 2\n''', encoding="utf-8")

(ROOT / "artifacts/wow-engine/tests/test_v17_daily_unique_prop_limit.py").write_text('''from __future__ import annotations\nfrom types import SimpleNamespace\nfrom v17.daily_snapshot_runtime import _prop_manifest_rows\n\nclass _Query:\n    def __init__(self, rows): self.rows=list(rows); self.start=0; self.end=len(self.rows)-1\n    def select(self, *_args, **_kwargs): return self\n    def eq(self, *_args, **_kwargs): return self\n    def order(self, *_args, **_kwargs): return self\n    def limit(self, n): self.start,self.end=0,n-1; return self\n    def range(self, start, end): self.start,self.end=start,end; return self\n    def execute(self): return SimpleNamespace(data=self.rows[self.start:self.end+1])\nclass _DB:\n    def __init__(self, rows): self.rows=rows\n    def table(self, name): assert name == "wow_prop_evidence_snapshots"; return _Query(self.rows)\n\ndef _row(snapshot_id, player, line):\n    return {"source_snapshot_id": snapshot_id, "captured_at": "2099-09-20T10:00:00Z", "event_id": f"evt-{player}", "event_start_time": "2099-09-20T20:00:00Z", "sport": "MLB", "player": player, "stat_type": "PITCHER_STRIKEOUTS", "line": line, "hydration_status": "PASS", "blockers": []}\n\ndef test_twenty_duplicate_snapshots_do_not_consume_four_prop_limit_slots():\n    rows=[_row(f"dup-{i}", "Cristopher Sanchez", 6.5) for i in range(20)] + [_row("u-1", "Pitcher Two", 5.5), _row("u-2", "Pitcher Three", 4.5), _row("u-3", "Pitcher Four", 7.5)]\n    manifest=_prop_manifest_rows(_DB(rows), "2099-09-20", "UTC")\n    limited=manifest[:4]\n    assert len(manifest) == 4\n    assert [row["player"] for row in limited] == ["Cristopher Sanchez", "Pitcher Two", "Pitcher Three", "Pitcher Four"]\n    assert limited[0]["source_instance_count"] == 20\n    assert len(limited[0]["source_snapshot_ids"]) == 20\n''', encoding="utf-8")

print("V17_COMPACT_DEDUP_PATCH_APPLIED=true")
