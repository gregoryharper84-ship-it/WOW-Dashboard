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
    baseline = dict(zip(m._METRICS, (4, 4, 0, 0, 0, 0, 0, 0, 2, 0)))
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
    assert len(seen) == 1
    paths, sport, season = seen[0]
    assert (sport, season) == ("NBA", 2025)
    assert paths != [str(child)]
    assert Path(paths[0]).name == "part-00000.parquet"
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
    {"invalid_market_flag": 1},
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
        description = [(name,) for name in sorted(m._REQUIRED_COLUMNS)]

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
    assert seen[0][1] == ["/tmp/one.parquet"]
    assert seen[1][1] == ["NBA' or 1=1 --", "2025", ["/tmp/one.parquet"]]
    assert "NBA' or 1=1" not in seen[1][0]
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
                  TRUE::BOOLEAN AS is_market_observation,
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


def test_source_mutated_between_hash_check_and_scan_fails_closed(tmp_path):
    root, child, manifest_path, _ = fixture(tmp_path)

    def malicious_or_racing_scan(*_):
        child.write_bytes(b"corpus changed while scanning")
        return metrics()

    with pytest.raises(m.PreflightHold, match="COLUMNAR_SOURCE_HASH_MISMATCH"):
        m.audit(manifest_path, root, scanner=malicious_or_racing_scan)


@pytest.mark.parametrize("char", ["*", "?", "[", "]"])
def test_globacters_are_rejected_before_any_scan(tmp_path, char):
    root, _, manifest_path, manifest = fixture(tmp_path)
    manifest["files"][0]["path"] = f"rows{char}.parquet"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(m.PreflightHold, match="COLUMNAR_MANIFEST_INVALID"):
        m.audit(manifest_path, root, scanner=lambda *_: metrics())


def test_temporary_source_change_and_restore_cannot_taint_snapshot(tmp_path):
    root, child, manifest_path, _ = fixture(tmp_path)
    frozen = child.read_bytes()

    def scan_pinned_bytes(paths, *_):
        assert paths != [str(child)]
        assert Path(paths[0]).read_bytes() == frozen
        child.write_bytes(b"out-of-snapshot mutation")
        assert Path(paths[0]).read_bytes() == frozen
        child.write_bytes(frozen)
        return metrics()

    result = m.audit(manifest_path, root, scanner=scan_pinned_bytes)
    assert result["status"] == "DATA_PREFLIGHT_PASS_ONLY"
    assert result["can_execute"] is False


def test_snapshot_integrity_failure_is_not_reported_as_pass(tmp_path):
    root, _, manifest_path, _ = fixture(tmp_path)

    def corrupt_snapshot(paths, *_):
        path = Path(paths[0])
        path.chmod(0o600)
        path.write_bytes(b"mutated private source")
        return metrics()

    with pytest.raises(m.PreflightHold, match="COLUMNAR_SOURCE_HASH_MISMATCH"):
        m.audit(manifest_path, root, scanner=corrupt_snapshot)


@pytest.mark.parametrize("is_market,quote_epoch,holds", [
    (True, "NULL::BIGINT", True),
    (False, "NULL::BIGINT", False),
    (True, "1729999000::BIGINT", False),
    (True, "1730000000::BIGINT", True),
    (True, "1730000001::BIGINT", True),
])
def test_market_quote_times_are_required_only_for_market_rows(tmp_path, is_market, quote_epoch, holds):
    duckdb = pytest.importorskip("duckdb")
    root = tmp_path / "corpus"
    root.mkdir()
    parquet = root / "NBA.parquet"
    con = duckdb.connect(database=":memory:")
    try:
        flag = "TRUE" if is_market else "FALSE"
        con.execute(f"""CREATE TABLE facts AS SELECT
            'NBA' AS sport, 2025::INTEGER AS season, 'obs-1' AS observation_id,
            'game-1' AS event_id, 1730000000::BIGINT AS event_start_epoch,
            1729999900::BIGINT AS feature_available_epoch,
            {flag}::BOOLEAN AS is_market_observation,
            {quote_epoch} AS quote_captured_epoch""")
        con.execute(f"COPY facts TO '{parquet}' (FORMAT PARQUET)")
    finally:
        con.close()
    manifest = {"schema": m.SCHEMA, "can_execute": False,
                "dataset_id": "nba_frozen", "sport": "NBA", "season": 2025,
                "files": [{"path": parquet.name,
                           "sha256": hashlib.sha256(parquet.read_bytes()).hexdigest()}]}
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    if holds:
        with pytest.raises(m.PreflightHold, match="COLUMNAR_INTEGRITY_HOLD"):
            m.audit(manifest_path, root)
    else:
        assert m.audit(manifest_path, root)["status"] == "DATA_PREFLIGHT_PASS_ONLY"


def test_parquet_columns_swapped_across_files_do_not_hide_future_features(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    root = tmp_path / "frozen"
    root.mkdir()
    con = duckdb.connect(database=":memory:")
    try:
        con.execute("""CREATE TABLE clean AS SELECT
            'NBA' AS sport, 2025::INTEGER AS season,
            'obs-clean' AS observation_id, 'event-clean' AS event_id,
            1730000000::BIGINT AS event_start_epoch,
            1729999900::BIGINT AS feature_available_epoch,
            TRUE::BOOLEAN AS is_market_observation,
            1729999000::BIGINT AS quote_captured_epoch""")
        con.execute("""CREATE TABLE future AS SELECT
            'NBA' AS sport, 2025::INTEGER AS season,
            'obs-future' AS observation_id, 'event-future' AS event_id,
            1730000100::BIGINT AS feature_available_epoch,
            1730000000::BIGINT AS event_start_epoch,
            TRUE::BOOLEAN AS is_market_observation,
            1729999000::BIGINT AS quote_captured_epoch""")
        for table, name in (("clean", "a.parquet"), ("future", "b.parquet")):
            con.execute(f"COPY {table} TO '{root / name}' (FORMAT PARQUET)")
    finally:
        con.close()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({
        "schema": m.SCHEMA, "can_execute": False, "dataset_id": "nba_2025",
        "sport": "NBA", "season": 2025,
        "files": [
            {"path": name, "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest()}
            for name in ("a.parquet", "b.parquet")
        ],
    }))
    with pytest.raises(m.PreflightHold, match="COLUMNAR_INTEGRITY_HOLD"):
        m.audit(manifest_path, root)


def test_parquet_scan_contract_uses_column_alignment_and_prestart_quotes():
    assert "union_by_name = true" in m._QUERY
    assert "quote_captured_epoch AS BIGINT) >= TRY_CAST(event_start_epoch" in m._QUERY


def test_each_parquet_file_is_checked_for_required_columns_even_without_duckdb(monkeypatch):
    calls = []

    class FakeSchema:
        description = [(name,) for name in sorted(m._REQUIRED_COLUMNS - {"quote_captured_epoch"})]

    class FakeConnection:
        def execute(self, sql, params):
            calls.append((sql, params))
            if sql.startswith("SELECT *"):
                return FakeSchema()
            raise AssertionError("aggregate must not run when per-file schema is incomplete")

        def close(self):
            calls.append("closed")

    class FakeDuckDB:
        def connect(self, database):
            assert database == ":memory:"
            return FakeConnection()

    monkeypatch.setattr(m.importlib, "import_module", lambda _: FakeDuckDB())
    with pytest.raises(m.PreflightHold, match="COLUMNAR_SCHEMA_INVALID"):
        m._duckdb_scan(["/frozen/nonmarket_missing_quote_column.parquet"], "NBA", 2025)
    assert calls[-1] == "closed"
    assert len(calls) == 2


def test_disappearing_source_root_produces_typed_hold(tmp_path, monkeypatch):
    root, _, manifest_path, _ = fixture(tmp_path)
    original_resolve = m.Path.resolve

    def disappear(self, strict=False):
        if self == root:
            raise FileNotFoundError("root disappeared")
        return original_resolve(self, strict=strict)

    monkeypatch.setattr(m.Path, "resolve", disappear)
    with pytest.raises(m.PreflightHold, match="COLUMNAR_SOURCE_UNAVAILABLE"):
        m.audit(manifest_path, root, scanner=lambda *_: metrics())


def test_per_file_schema_contract_is_mandatory():
    assert m._REQUIRED_COLUMNS == frozenset({
        "sport", "season", "observation_id", "event_id", "event_start_epoch",
        "feature_available_epoch", "is_market_observation", "quote_captured_epoch",
    })


def test_missing_nonmarket_quote_column_in_second_parquet_is_schema_hold(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    root = tmp_path / "frozen"
    root.mkdir()
    con = duckdb.connect(database=":memory:")
    try:
        con.execute("""CREATE TABLE complete AS SELECT 'NBA' AS sport,
            2025::INTEGER AS season, 'obs-1' AS observation_id,
            'event-1' AS event_id, 1730000000::BIGINT AS event_start_epoch,
            1729999900::BIGINT AS feature_available_epoch,
            TRUE::BOOLEAN AS is_market_observation,
            1729999000::BIGINT AS quote_captured_epoch""")
        con.execute("""CREATE TABLE incomplete AS SELECT 'NBA' AS sport,
            2025::INTEGER AS season, 'obs-2' AS observation_id,
            'event-2' AS event_id, 1730000000::BIGINT AS event_start_epoch,
            1729999900::BIGINT AS feature_available_epoch,
            FALSE::BOOLEAN AS is_market_observation""")
        for table, name in (("complete", "a.parquet"), ("incomplete", "b.parquet")):
            con.execute(f"COPY {table} TO '{root / name}' (FORMAT PARQUET)")
    finally:
        con.close()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({
        "schema": m.SCHEMA, "can_execute": False, "dataset_id": "nba_2025",
        "sport": "NBA", "season": 2025,
        "files": [
            {"path": name, "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest()}
            for name in ("a.parquet", "b.parquet")
        ],
    }))
    with pytest.raises(m.PreflightHold, match="COLUMNAR_SCHEMA_INVALID"):
        m.audit(manifest_path, root)
