from pathlib import Path

import pytest

from v17 import source_coverage_audit as sca
from v17.free_source_catalog import SOURCES as CATALOG
from v17.model_source_entitlements import SOURCES as REGISTRY

ROOT = Path(__file__).parent


def test_matrix_is_deterministic_and_never_grants_authority():
    a = sca.build_source_matrix(ROOT)
    assert a == sca.build_source_matrix(ROOT)
    assert a and all(r["can_execute"] is False and r["probability_source"] is False for r in a)


def test_catalog_entry_alone_is_never_acquisition_or_training_authorization():
    rows = sca.build_source_matrix(ROOT)
    assert not any(r["legal_reproducible_fitted_training"] for r in rows)
    for r in rows:
        assert sca.B_NO_LIVE_RECEIPT in r["blockers"]
        assert sca.B_NO_LICENSE_RECEIPT in r["blockers"]


def test_registry_ready_flag_does_not_imply_legal_training():
    rows = sca.build_source_matrix(ROOT)
    ready = [r for r in rows if r["candidate_training_ready"]]
    assert ready  # e.g. open-licensed sources
    assert all(sca.B_CERT_REVIEW in r["blockers"] for r in ready)


def test_undocumented_first_party_sources_carry_rights_blocker():
    rows = {(r["sport"], r["source_id"]): r for r in sca.build_source_matrix(ROOT)}
    assert sca.B_RIGHTS_UNDOCUMENTED in rows[("NHL", "NHL_PUBLIC_WEB_API")]["blockers"]
    assert sca.B_RIGHTS_UNDOCUMENTED in rows[("MMA", "UFCSTATS_PUBLIC")]["blockers"]


def test_claimed_receipt_ids_never_authorize_undocumented_or_unclassified_training():
    """Evidence-pointer presence cannot override a missing legal source entitlement."""
    sources = ("NHL_PUBLIC_WEB_API", "UFCSTATS_PUBLIC", "MLB_STATS_API", "NFLVERSE")
    rows = sca.build_source_matrix(
        ROOT,
        live_receipts={source: "unreviewed-live-pointer" for source in sources},
        license_receipts={source: "unreviewed-terms-pointer" for source in sources},
    )
    for source in sources:
        matches = [row for row in rows if row["source_id"] == source]
        assert matches, source
        for row in matches:
            assert not row["legal_reproducible_fitted_training"]
            if source in ("NHL_PUBLIC_WEB_API", "UFCSTATS_PUBLIC"):
                assert sca.B_RIGHTS_UNDOCUMENTED in row["blockers"]
            else:
                assert sca.B_UNCLASSIFIED in row["blockers"]
            assert row["probability_source"] is False
            assert row["can_execute"] is False


def test_catalog_sources_without_registry_entry_are_flagged_unclassified():
    rows = sca.build_source_matrix(ROOT)
    for s in CATALOG:
        if s.registry_source_id is None:
            matching = [r for r in rows if r["source_id"] == s.source_id]
            assert matching and all(sca.B_UNCLASSIFIED in r["blockers"] for r in matching)


def test_every_registry_source_appears_for_each_declared_sport():
    keys = {(r["sport"], r["source_id"]) for r in sca.build_source_matrix(ROOT)}
    for rid, reg in REGISTRY.items():
        for sport in reg.sports:
            assert (sport, rid) in keys


def test_receipts_remove_only_their_own_blockers_and_nothing_else():
    rows = sca.build_source_matrix(
        ROOT,
        live_receipts={"SPORTSDATAVERSE_ESPN": "rcpt-live-1"},
        license_receipts={"SPORTSDATAVERSE_ESPN": "rcpt-lic-1"},
    )
    espn = [r for r in rows if r["source_id"] == "SPORTSDATAVERSE_ESPN"]
    assert espn and all(sca.B_NO_LIVE_RECEIPT not in r["blockers"] for r in espn)
    assert all(sca.B_CERT_REVIEW in r["blockers"] for r in espn)  # still not legal-ready
    assert not any(r["legal_reproducible_fitted_training"] for r in espn)


def test_url_adapter_reference_is_not_counted_as_wow_adapter():
    rows = [r for r in sca.build_source_matrix(ROOT) if r["source_id"] == "ODDSPAPI"]
    assert rows and all(sca.B_NO_ADAPTER in r["blockers"] for r in rows)


def test_failure_path_unknown_sport_fails_closed(monkeypatch):
    from dataclasses import replace
    bad = REGISTRY["BALLDONTLIE"]
    monkeypatch.setitem(REGISTRY, "BALLDONTLIE", replace(bad, sports=("CURLING",)))
    with pytest.raises(RuntimeError, match="SOURCE_COVERAGE_UNKNOWN_SPORT"):
        sca.build_source_matrix(ROOT)


def test_research_tool_usage_ast_scan_ignores_strings_and_comments(tmp_path):
    (tmp_path / "a.py").write_text('# import duckdb\nx = "import mlflow"\nimport optuna\nfrom evidently.report import Report\n')
    (tmp_path / "broken.py").write_text("def (:\n")
    u = sca.research_tool_usage(tmp_path)
    assert u["optuna"] == ["a.py"] and u["evidently"] == ["a.py"]
    assert u["duckdb"] == [] and u["mlflow"] == []


def test_sport_summary_counts():
    s = sca.sport_summary(sca.build_source_matrix(ROOT))
    assert s["NHL"]["candidate_training_ready"] >= 1
    assert all(v["legal_reproducible_fitted_training"] == 0 for v in s.values())


def test_repo_root_relative_adapter_references_resolve_from_relative_root():
    rows = sca.build_source_matrix(".")
    odds = [r for r in rows if r["source_id"] == "THE_ODDS_API"]
    assert odds and all(sca.B_NO_ADAPTER not in r["blockers"] for r in odds)


def test_registry_only_sources_do_not_gain_an_adapter_by_omission():
    rows = sca.build_source_matrix(ROOT)
    ufc = [r for r in rows if r["source_id"] == "UFCSTATS_PUBLIC"]
    assert ufc and all(sca.B_NO_ADAPTER in r["blockers"] for r in ufc)


def test_adapter_lookup_rejects_basename_collisions_and_traversal(tmp_path):
    (tmp_path / "v17").mkdir()
    (tmp_path / "v17" / "odds_api.py").write_text("not the actual referenced file")
    assert not sca._adapter_exists("artifacts/flask-scoring-api/services/odds_api.py", tmp_path)
    assert not sca._adapter_exists("../outside.py", tmp_path)
    assert not sca._adapter_exists("https://example.org/odds_api.py", tmp_path)


def test_adapter_lookup_same_for_repository_root_and_engine_root(tmp_path):
    engine = tmp_path / "artifacts" / "wow-engine"
    engine.mkdir(parents=True)
    (engine / "nflverse_historical_adapter.py").write_text("adapter")
    (engine / "v17").mkdir()
    (engine / "v17" / "ncaaf_sdv_espn_crosswalk.py").write_text("adapter")
    for root in (tmp_path, engine):
        assert sca._adapter_exists("nflverse_historical_adapter.py", root)
        assert sca._adapter_exists("v17/ncaaf_sdv_espn_crosswalk.py", root)


def test_research_tool_scan_ignores_vendor_tree(tmp_path):
    (tmp_path / "venv").mkdir()
    (tmp_path / "venv" / "vendor.py").write_text("import mlflow")
    (tmp_path / "first_party.py").write_text("import evidently")
    uses = sca.research_tool_usage(tmp_path)
    assert uses["mlflow"] == []
    assert uses["evidently"] == ["first_party.py"]
