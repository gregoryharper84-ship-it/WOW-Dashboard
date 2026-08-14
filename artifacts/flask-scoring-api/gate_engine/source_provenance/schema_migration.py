"""
gate_engine/source_provenance/schema_migration.py

Backward-compatible DDL migration for WOW-PATCH-2026-08-14-SOURCE-PROVENANCE.

Changes applied:
  1. llp_source_snapshots  — ADD COLUMN IF NOT EXISTS for the 16 new provenance
                             fields that were missing from the original schema.
  2. uac_evidence_packets  — Same 16 columns added for the UAC evidence path.
  3. llp_calibration_ledger — Fix the FK inconsistency: source_snapshot_id is
                             currently bare TEXT (no FK); llp_event_candidates
                             already has the proper FK.  This migration adds
                             the constraint as NOT VALID (no table scan, no lock)
                             so existing rows with orphaned snapshot IDs are not
                             rejected.

All DDL is idempotent:
  - ADD COLUMN IF NOT EXISTS  — safe to run multiple times.
  - The FK is added inside a DO $$ ... EXCEPTION WHEN duplicate_object ... $$
    block so re-running never raises an error.

This migration does NOT drop, rename, or change the type of any existing column.
It does NOT touch Command Center, SkillOrchestrator, LLP scoring logic, or the
probability ledger enforcer.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# The 16 new provenance columns and their types.
# Listed once; applied to both llp_source_snapshots and uac_evidence_packets.
_PROVENANCE_COLUMNS: list[tuple[str, str]] = [
    ("fact_type",               "TEXT"),
    ("fact_value_hash",         "TEXT"),
    ("source_grade",            "TEXT"),
    ("published_at",            "TIMESTAMPTZ"),
    ("observed_at",             "TIMESTAMPTZ"),
    ("effective_at",            "TIMESTAMPTZ"),
    ("valid_until",             "TIMESTAMPTZ"),
    ("freshness_policy_id",     "TEXT"),
    ("freshness_basis",         "TEXT"),
    ("freshness_status",        "TEXT"),
    ("materiality",             "TEXT"),
    ("supports_checkpoint",     "TEXT[]"),
    ("conflicts_with",          "TEXT[]"),
    ("conflict_status",         "TEXT"),
    ("reconstruction_status",   "TEXT"),
    ("max_supportable_ceiling", "TEXT"),
]

# Indexes that help downstream queries on freshness / conflict status
_PROVENANCE_INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS llp_source_snapshots_freshness_status_idx "
    "ON llp_source_snapshots (freshness_status)",

    "CREATE INDEX IF NOT EXISTS llp_source_snapshots_conflict_status_idx "
    "ON llp_source_snapshots (conflict_status)",

    "CREATE INDEX IF NOT EXISTS llp_source_snapshots_max_ceiling_idx "
    "ON llp_source_snapshots (max_supportable_ceiling) "
    "WHERE max_supportable_ceiling IS NOT NULL",

    "CREATE INDEX IF NOT EXISTS uac_evidence_packets_freshness_status_idx "
    "ON uac_evidence_packets (freshness_status)",

    "CREATE INDEX IF NOT EXISTS uac_evidence_packets_conflict_status_idx "
    "ON uac_evidence_packets (conflict_status)",

    "CREATE INDEX IF NOT EXISTS uac_evidence_packets_max_ceiling_idx "
    "ON uac_evidence_packets (max_supportable_ceiling) "
    "WHERE max_supportable_ceiling IS NOT NULL",
]

# FK fix: llp_calibration_ledger.source_snapshot_id → llp_source_snapshots(snapshot_id)
# NOT VALID is intentional: existing rows may have orphaned IDs since
# llp_source_snapshots was never written to before this patch.  NOT VALID adds
# the constraint without scanning existing rows; future inserts/updates are
# validated.  Run VALIDATE CONSTRAINT separately once the data is clean.
_FK_FIX_DDL = """
DO $$
BEGIN
    ALTER TABLE llp_calibration_ledger
        ADD CONSTRAINT fk_llp_calibration_source_snapshot
        FOREIGN KEY (source_snapshot_id)
        REFERENCES llp_source_snapshots(snapshot_id)
        ON DELETE SET NULL
        NOT VALID;
EXCEPTION
    WHEN duplicate_object THEN NULL;
END;
$$;
"""


def run_provenance_migration(conn) -> dict[str, Any]:
    """
    Apply the WOW-PATCH-2026-08-14-SOURCE-PROVENANCE DDL migrations.

    Parameters
    ----------
    conn : psycopg2 connection (or compatible)

    Returns
    -------
    dict with keys:
        success       : bool
        columns_added : list[str]  — "table.column" for each ADD COLUMN executed
        indexes_added : int
        fk_applied    : bool
        errors        : list[str]
    """
    columns_added: list[str] = []
    errors: list[str] = []
    indexes_added = 0
    fk_applied = False

    try:
        cur = conn.cursor()

        # ── 1. Add provenance columns to llp_source_snapshots ──────────────
        for col_name, col_type in _PROVENANCE_COLUMNS:
            try:
                cur.execute(
                    f"ALTER TABLE llp_source_snapshots "
                    f"ADD COLUMN IF NOT EXISTS {col_name} {col_type};"
                )
                columns_added.append(f"llp_source_snapshots.{col_name}")
            except Exception as exc:
                errors.append(f"llp_source_snapshots.{col_name}: {exc}")
                log.warning("provenance_migration: column add failed: %s", exc)

        # ── 2. Add provenance columns to uac_evidence_packets ───────────────
        for col_name, col_type in _PROVENANCE_COLUMNS:
            try:
                cur.execute(
                    f"ALTER TABLE uac_evidence_packets "
                    f"ADD COLUMN IF NOT EXISTS {col_name} {col_type};"
                )
                columns_added.append(f"uac_evidence_packets.{col_name}")
            except Exception as exc:
                errors.append(f"uac_evidence_packets.{col_name}: {exc}")
                log.warning("provenance_migration: column add failed: %s", exc)

        # ── 3. Add provenance indexes ────────────────────────────────────────
        for idx_sql in _PROVENANCE_INDEXES:
            try:
                cur.execute(idx_sql)
                indexes_added += 1
            except Exception as exc:
                errors.append(f"index: {exc}")
                log.warning("provenance_migration: index creation failed: %s", exc)

        # ── 4. Fix FK on llp_calibration_ledger ─────────────────────────────
        try:
            cur.execute(_FK_FIX_DDL)
            fk_applied = True
        except Exception as exc:
            errors.append(f"fk_fix: {exc}")
            log.warning("provenance_migration: FK fix failed: %s", exc)

        conn.commit()
        cur.close()

        log.info(
            "provenance_migration: complete. columns_added=%d indexes=%d fk=%s errors=%d",
            len(columns_added), indexes_added, fk_applied, len(errors),
        )
        return {
            "success":       len(errors) == 0,
            "columns_added": columns_added,
            "indexes_added": indexes_added,
            "fk_applied":    fk_applied,
            "errors":        errors,
        }

    except Exception as exc:
        log.error("provenance_migration: fatal error: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return {
            "success":       False,
            "columns_added": columns_added,
            "indexes_added": indexes_added,
            "fk_applied":    fk_applied,
            "errors":        errors + [f"fatal: {exc}"],
        }


def generate_migration_sql() -> str:
    """
    Return the migration DDL as a plain SQL string for offline inspection
    or manual execution without a live connection.
    """
    parts: list[str] = [
        "-- WOW-PATCH-2026-08-14-SOURCE-PROVENANCE-FRESHNESS-AUDITOR-v2 migration",
        "-- Generated by gate_engine/source_provenance/schema_migration.py",
        "",
        "-- 1. Provenance columns for llp_source_snapshots",
    ]
    for col_name, col_type in _PROVENANCE_COLUMNS:
        parts.append(
            f"ALTER TABLE llp_source_snapshots "
            f"ADD COLUMN IF NOT EXISTS {col_name} {col_type};"
        )

    parts += [
        "",
        "-- 2. Provenance columns for uac_evidence_packets",
    ]
    for col_name, col_type in _PROVENANCE_COLUMNS:
        parts.append(
            f"ALTER TABLE uac_evidence_packets "
            f"ADD COLUMN IF NOT EXISTS {col_name} {col_type};"
        )

    parts += [
        "",
        "-- 3. Indexes",
    ]
    parts.extend(_PROVENANCE_INDEXES)

    parts += [
        "",
        "-- 4. FK fix: llp_calibration_ledger.source_snapshot_id",
        _FK_FIX_DDL.strip(),
    ]

    return "\n".join(parts)
