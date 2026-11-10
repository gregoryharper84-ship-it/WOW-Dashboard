"""Read-only, offline source-coverage and rights audit for V17 research tooling.

Derives its matrix ONLY from the existing entitlement registry
(``model_source_entitlements``) and free-source catalog (``free_source_catalog``).
It is not a registry, fetcher, license grant, training authorization, or
probability source. No network, storage, or secrets are touched. A catalog or
registry entry is never treated as proof of live availability or of a verified
license: both require an explicit receipt, and none is accepted here by default.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Iterable, Mapping

from v17.free_source_catalog import SOURCES as CATALOG, SPORTS
from v17.model_source_entitlements import SOURCES as REGISTRY, source_readiness

CAN_EXECUTE = False
AUDIT_VERSION = "WOW_V17_SOURCE_COVERAGE_AUDIT_V1"
RESEARCH_TOOLS = ("mlflow", "duckdb", "evidently", "optuna")

B_NO_LIVE_RECEIPT = "LIVE_ENDPOINT_RECEIPT_ABSENT"
B_NO_LICENSE_RECEIPT = "LICENSE_VERIFICATION_RECEIPT_ABSENT"
B_UNCLASSIFIED = "NOT_IN_ENTITLEMENT_REGISTRY"
B_NO_ADAPTER = "ADAPTER_REFERENCE_NOT_FOUND"
B_CERT_REVIEW = "CERTIFICATION_SOURCE_REVIEW_REQUIRED"
B_RIGHTS_UNDOCUMENTED = "RIGHTS_UNDOCUMENTED_FIRST_PARTY"
B_NONCOMMERCIAL = "TERMS_RESTRICT_NONCOMMERCIAL"
B_RECEIPT_WEAK = "LICENSE_RECEIPT_NOT_PROBATIVE"
B_UPSTREAM = "UPSTREAM_RIGHTS_UNVERIFIED"
B_TIMESTAMP_GAP = "PUBLICATION_TIMESTAMP_ABSENT"
RECEIPT_SCHEMA = "WOW_SOURCE_RIGHTS_RECEIPTS_V1"


def load_rights_receipts(path: str | Path) -> dict[str, list[dict]]:
    """Load typed rights findings keyed by source_id. Fails closed on bad shape."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if raw.get("schema") != RECEIPT_SCHEMA or raw.get("can_execute") is not False:
        raise ValueError("SOURCE_RIGHTS_RECEIPT_SCHEMA_INVALID")
    out: dict[str, list[dict]] = {}
    for item in raw.get("receipts", []):
        for key in ("source_id", "finding", "evidence_grade", "url", "excerpt_sha256"):
            if not item.get(key):
                raise ValueError(f"SOURCE_RIGHTS_RECEIPT_FIELD_MISSING:{key}")
        out.setdefault(item["source_id"], []).append(item)
    return out


def unmatched_receipts(rows: Iterable[dict], receipts: Mapping[str, list[dict]]) -> list[str]:
    known = {r["source_id"] for r in rows}
    return sorted(set(receipts) - known)


def _adapter_exists(ref: str, engine_root: Path) -> bool:
    if ref.startswith("http"):
        return False  # a URL is not a WOW adapter
    for base in (engine_root, engine_root / "v17", engine_root.parent.parent):
        if (base / ref).is_file():
            return True
    return (engine_root / ref.removeprefix("v17/")).is_file() or (engine_root / "v17" / Path(ref).name).is_file()


def build_source_matrix(
    engine_root: str | Path,
    *,
    live_receipts: Mapping[str, str] | None = None,
    license_receipts: Mapping[str, str] | None = None,
    rights_receipts: Mapping[str, list[dict]] | None = None,
) -> list[dict]:
    """One row per (sport, source). Receipts are source_id -> evidence id."""
    root = Path(engine_root).resolve()
    live = dict(live_receipts or {})
    lic = dict(license_receipts or {})
    findings = dict(rights_receipts or {})
    rows: list[dict] = []
    catalog_by_reg = {s.registry_source_id: s for s in CATALOG if s.registry_source_id}
    seen: set[tuple[str, str]] = set()

    def emit(sport: str, source_id: str, cat, reg_id: str | None) -> None:
        if (sport, source_id) in seen:
            return
        seen.add((sport, source_id))
        reg = REGISTRY.get(reg_id) if reg_id else None
        readiness = source_readiness(reg_id) if reg_id else None
        blockers: list[str] = []
        if reg is None:
            blockers.append(B_UNCLASSIFIED)
        else:
            blockers.extend(readiness.blockers)
            if reg.certification_source_review_required:
                blockers.append(B_CERT_REVIEW)
            if reg.use == "CANDIDATE_FIRST_PARTY_UNDOCUMENTED":
                blockers.append(B_RIGHTS_UNDOCUMENTED)
        if cat is not None and not _adapter_exists(cat.adapter_reference, root):
            blockers.append(B_NO_ADAPTER)
        if source_id not in live:
            blockers.append(B_NO_LIVE_RECEIPT)
        found = findings.get(source_id, [])
        probative = any(f["finding"] == "PERMITS_TRAINING" and f["evidence_grade"] == "RAW_BYTES" for f in found)
        if source_id not in lic and not probative:
            blockers.append(B_RECEIPT_WEAK if found else B_NO_LICENSE_RECEIPT)
        if any(f["finding"] == "RESTRICTS_NONCOMMERCIAL" for f in found):
            blockers.append(B_NONCOMMERCIAL)
        if any(f.get("upstream_unverified") for f in found):
            blockers.append(B_UPSTREAM)
        if any(f.get("timestamp_gap") for f in found):
            blockers.append(B_TIMESTAMP_GAP)
        rows.append({
            "sport": sport,
            "source_id": source_id,
            "in_catalog": cat is not None,
            "catalog_integration": getattr(cat, "integration", None),
            "catalog_rights_state": getattr(cat, "rights_state", None),
            "adapter_reference": getattr(cat, "adapter_reference", None),
            "in_registry": reg is not None,
            "registry_use": getattr(reg, "use", None),
            "license_id": getattr(reg, "license_id", None),
            "license_url": getattr(reg, "license_url", None),
            "attribution_required": getattr(reg, "attribution_required", None),
            "candidate_training_ready": bool(readiness and readiness.ready_for_candidate_training),
            "live_endpoint_receipt": live.get(source_id),
            "license_verification_receipt": lic.get(source_id),
            "rights_findings": [f["finding"] for f in found],
            "blockers": sorted(set(blockers)),
            # Legal, reproducible fitted training needs NO blockers at all.
            "legal_reproducible_fitted_training": not blockers,
            "probability_source": False,
            "can_execute": False,
        })

    for s in CATALOG:
        for sport in s.sports:
            emit(sport, s.source_id, s, s.registry_source_id)
    for reg_id, reg in REGISTRY.items():
        for sport in reg.sports:
            emit(sport, reg_id, catalog_by_reg.get(reg_id), reg_id)
    unknown = {r["sport"] for r in rows} - set(SPORTS)
    if unknown:
        raise RuntimeError(f"SOURCE_COVERAGE_UNKNOWN_SPORT:{sorted(unknown)}")
    return sorted(rows, key=lambda r: (r["sport"], r["source_id"]))


def sport_summary(rows: Iterable[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows:
        s = out.setdefault(r["sport"], {"sources": 0, "candidate_training_ready": 0,
                                        "legal_reproducible_fitted_training": 0})
        s["sources"] += 1
        s["candidate_training_ready"] += int(r["candidate_training_ready"])
        s["legal_reproducible_fitted_training"] += int(r["legal_reproducible_fitted_training"])
    return out


def research_tool_usage(root: str | Path) -> dict[str, list[str]]:
    """AST scan: which .py files actually import each research tool (no exec)."""
    found: dict[str, list[str]] = {t: [] for t in RESEARCH_TOOLS}
    base = Path(root)
    for path in sorted(base.rglob("*.py")):
        if any(p in {"node_modules", ".venv", "__pycache__"} for p in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module.split(".")[0]]
            for n in names:
                if n in found:
                    rel = str(path.relative_to(base))
                    if rel not in found[n]:
                        found[n].append(rel)
    return found


__all__ = ["AUDIT_VERSION", "CAN_EXECUTE", "build_source_matrix", "load_rights_receipts", "unmatched_receipts", "research_tool_usage", "sport_summary"]
