"""Synthetic-only regressions for the optional columnar preflight."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).parents[1] / "v17" / "research_columnar_preflight.py"
spec = importlib.util.spec_from_file_location("research_columnar_preflight", SOURCE)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


def fixture(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    child = root / "NBA_2025.parquet"
    child.write_bytes(b"synthetic Parquet placeholder; scanner is injected")
    manifest = {"schema": m.SCHEMA, "can_execute": False, "dataset_id": "basketball_2025",
                "sport": "NBA", "season": 2025,
                "files": [{"path": child.name, "sha256": hashlib.sha256(child.read_bytes()).hexdigest()}]}
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    return root, child, manifest_path, manifest


def metrics(**changes):
    baseline = dict(zip(m._METRICS, (4, 4, 0, 0, 0, 0, 0, 2, 0)))
    baseline.update(changes)
    return baseline


def test_valid_inputs_only_produce_a_non_authorizing_receipt(tmp_path):
    root, child, manifest_path, _ = fixture(tmp_path)
    seen = []

    def scanner(paths, sport, season):
        seen.append((paths, sport, season))
        return metrics()

    out = m.audit(manifest_path, root, scanner=scanner)
    assert out["status"] == "DATA_PREFLIGHT_PASS_ONLY"
    assert out["metrics"]["quoted_rows"] == 2
    assert seen == [([str(child)], "NBA", 2025)]
    for flag in ("training_authorized", "model_fitted", "certification_eligible",
                 "probability_publishable", "can_execute"):
        assert out[flag] is False


@pytest.mark.parametrize("mutated", [
    {"distinct_observations": 3},
    {"missing_identity": 1},
    {"wrong_partition": 1},
    {"invalid_event_time": 1},
    {"missing_feature_time": 1},
    {"features_after_start": 1},
    {"invalid_quote_time": 1},
])
def test_fail_closed_for_duplicate_identity_or_point_in_time_leak(tmp_path, mutated):
    root, _, manifest_path, _ = fixture(tmp_path)
    with pytest.raises(m.PreflightHold, match="COLUMNAR_INTEGRITY_HOLD"):
        m.audit(manifest_path, root, scanner=lambda *_: metrics(**mutated))


def test_changed_source_is_rejected_before_a_database_scan(tmp_path):
    root, child, manifest_path, _ = fixture(tmp_path)
    child.write_bytes(b"source was modified")

    def should_not_call(*_):
        raise AssertionError("unexpected scan")

    with pytest.raises(m.PreflightHold, match="COLUMNAR_SOURCE_HASH_MISMATCH"):
        m.audit(manifest_path, root, scanner=should_not_call)


def test_path_escape_and_symlink_are_rejected(tmp_path):
    root, child, manifest_path, manifest = fixture(tmp_path)
    manifest["files"][0]["path"] = "../escape.parquet"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(m.PreflightHold, match="COLUMNAR_MANIFEST_INVALID"):
        m.audit(manifest_path, root, scanner=lambda *_: metrics())
    manifest["files"][0]["path"] = "alias.parquet"
    (root / "alias.parquet").symlink_to(child)
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(m.PreflightHold, match="COLUMNAR_SOURCE_UNAVAILABLE"):
        m.audit(manifest_path, root, scanner=lambda *_: metrics())


def test_cannot_claim_training_authorization_in_manifest(tmp_path):
    root, _, manifest_path, manifest = fixture(tmp_path)
    manifest["training_authorized"] = True
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(m.PreflightHold, match="COLUMNAR_MANIFEST_INVALID"):
        m.audit(manifest_path, root, scanner=lambda *_: metrics())


def test_empty_or_incoherent_database_output_fails_closed(tmp_path):
    root, _, manifest_path, _ = fixture(tmp_path)
    for output, reason in ((metrics(row_count=0, distinct_observations=0), "COLUMNAR_EMPTY_CORPUS"),
                           (metrics(quoted_rows=5), "COLUMNAR_SCAN_INVALID"),
                           ({"row_count": 1}, "COLUMNAR_SCAN_INVALID")):
        with pytest.raises(m.PreflightHold, match=reason):
            m.audit(manifest_path, root, scanner=lambda *_, out=output: out)


def test_database_sql_uses_bound_values_and_closes_session(monkeypatch):
    seen = []

    class FakeCursor:
        def fetchone(self):
            return tuple(metrics().values())

    class FakeConnection:
        def execute(self, sql, params):
            seen.append((sql, params))
            return FakeCursor()

        def close(self):
            seen.append("closed")

    class FakeDuckDB:
        def connect(self, database):
            assert database == ":memory:"
            return FakeConnection()

    monkeypatch.setattr(m.importlib, "import_module", lambda _: FakeDuckDB())
    out = m._duckdb_scan(["/tmp/one.parquet"], "NBA' or 1=1 --", 2025)
    assert out["row_count"] == 4
    assert seen[0][1] == ["NBA' or 1=1 --", "2025", ["/tmp/one.parquet"]]
    assert "NBA' or 1=1" not in seen[0][0]
    assert seen[-1] == "closed"


def test_missing_optional_duckdb_dependency_is_a_typed_hold(monkeypatch):
    def missing(_):
        raise ImportError("duckdb not installed")

    monkeypatch.setattr(m.importlib, "import_module", missing)
    with pytest.raises(m.PreflightHold, match="COLUMNAR_DUCKDB_UNAVAILABLE"):
        m._duckdb_scan(["/tmp/file.parquet"], "NBA", 2025)


@pytest.mark.parametrize("future_feature", [False, True])
def test_real_duckdb_parquet_scan_when_research_dependency_present(tmp_path, future_feature):
    duckdb = pytest.importorskip("duckdb")
    root = tmp_path / "frozen"
    root.mkdir()
    parquet = root / "NBA_2025.parquet"
    con = duckdb.connect(database=":memory:")
    try:
        feature_epoch = 1730000100 if future_feature else 1729999900
        con.execute("""CREATE TABLE frozen AS SELECT 'NBA' AS sport,
                  2025::INTEGER AS season, 'nba-1' AS observation_id,
                  'game-1' AS event_id, 1730000000::BIGINT AS event_start_epoch,
                  ?::BIGINT AS feature_available_epoch,
                  1729999000::BIGINT AS quote_captured_epoch""", [feature_epoch])
        con.execute(f"COPY frozen TO '{parquet}' (FORMAT PARQUET)")
    finally:
        con.close()
    manifest_path = tmp_path / "frozen_manifest.json"
    manifest_path.write_text(json.dumps({
        "schema": m.SCHEMA, "can_execute": False, "dataset_id": "nba_frozen",
        "sport": "NBA", "season": 2025,
        "files": [{"path": parquet.name, "sha256": hashlib.sha256(parquet.read_bytes()).hexdigest()}],
    }))
    if future_feature:
        with pytest.raises(m.PreflightHold, match="COLUMNAR_INTEGRITY_HOLD"):
            m.audit(manifest_path, root)
    else:
        outcome = m.audit(manifest_path, root)
        assert outcome["metrics"]["row_count"] == 1
        assert outcome["metrics"]["quoted_rows"] == 1
        assert outcome["probability_publishable"] is False
