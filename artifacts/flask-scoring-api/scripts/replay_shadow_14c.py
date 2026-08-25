#!/usr/bin/env python3
"""
scripts/replay_shadow_14c.py
Step 14C — Offline replay of 125 persisted pilot outputs.

WHAT THIS DOES (zero new Anthropic API calls)
  1. Reads all 125 existing rows from kalshi_wx_shadow_results.
  2. Runs each row's stored agent_output through the new native per-subagent
     validator (gate_engine.kalshi_wx_shadow_native_schema).
  3. Groups the 125 rows into 25 snapshot groups (one per research_snapshot_id).
  4. For each group, calls the existing _assemble_payload() from
     gate_engine.kalshi_wx_shadow_orchestrator and then validate_shadow_output()
     from gate_engine.kalshi_wx_shadow_schema — exactly as PART B wires it
     into the live runner.
  5. Upserts each snapshot's result into kalshi_wx_shadow_snapshot_schema_validation.
  6. Prints a complete pass/fail breakdown: 125 native + 25 canonical.

DOES NOT
  - Modify or fix any of the 125 historical agent_output rows.
  - Make any Anthropic API calls.
  - Import app.py or any Flask/gunicorn machinery.

Run as:
    python scripts/replay_shadow_14c.py
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import sys
from collections import defaultdict
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
_log = logging.getLogger("replay_shadow_14c")

# ── Ordered agent sequence (must match AGENT_IDS in the pilot runner) ─────────
AGENT_IDS: List[str] = [
    "forecast_context",
    "source_reconciliation",
    "contradiction_detection",
    "unusual_regime",
    "uncertainty_explanation",
]


# ── Minimal SubagentResult stand-in for _assemble_payload() ──────────────────
# _assemble_payload() only reads .success and .tool_input on each result.
@dataclasses.dataclass
class _FakeResult:
    success: bool
    tool_input: dict
    hook_violations: list = dataclasses.field(default_factory=list)


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_conn():
    import psycopg2  # noqa: PLC0415
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(url, connect_timeout=10)


def _fetch_all_rows(conn) -> List[Dict]:
    """
    Read all rows from kalshi_wx_shadow_results, joined with the snapshot queue
    to get city and market_date for assembly.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                r.research_snapshot_id,
                r.agent_id,
                r.status,
                r.validated_output_json,
                q.snapshot_json->>'city'        AS city,
                q.snapshot_json->>'market_date' AS market_date
            FROM kalshi_wx_shadow_results r
            LEFT JOIN kalshi_wx_shadow_snapshot_queue q
                   ON q.research_snapshot_id = r.research_snapshot_id
            ORDER BY r.research_snapshot_id, r.agent_id
        """)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _upsert_validation_row(
    conn,
    rsid: str,
    canonical_payload: Optional[dict],
    validation_status: str,
    validation_detail: Optional[str],
) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO kalshi_wx_shadow_snapshot_schema_validation
                (research_snapshot_id, canonical_payload_json,
                 validation_status, validation_detail)
            VALUES (%s, %s::jsonb, %s, %s)
            ON CONFLICT (research_snapshot_id) DO UPDATE SET
                canonical_payload_json = EXCLUDED.canonical_payload_json,
                validation_status      = EXCLUDED.validation_status,
                validation_detail      = EXCLUDED.validation_detail,
                recorded_at            = NOW()
        """, (
            rsid,
            json.dumps(canonical_payload) if canonical_payload is not None else None,
            validation_status,
            validation_detail,
        ))
    conn.commit()


# ── Main replay logic ─────────────────────────────────────────────────────────

def main() -> None:
    from gate_engine.kalshi_wx_shadow_native_schema import validate_subagent_output  # noqa: PLC0415
    from gate_engine.kalshi_wx_shadow_orchestrator import _assemble_payload          # noqa: PLC0415
    from gate_engine.kalshi_wx_shadow_schema import validate_shadow_output           # noqa: PLC0415

    conn = _get_conn()
    try:
        rows = _fetch_all_rows(conn)
    except Exception as exc:
        _log.error("Failed to fetch rows: %s", exc)
        conn.close()
        sys.exit(1)

    _log.info("Fetched %d rows from kalshi_wx_shadow_results", len(rows))

    # ── Step 1+2: Native validation for each of the 125 rows ─────────────────
    native_pass = 0
    native_fail = 0
    native_failures: List[Dict] = []

    # Group by rsid for later assembly
    by_rsid: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"city": "", "market_date": "", "agents": {}})

    for row in rows:
        rsid       = row["research_snapshot_id"]
        agent_id   = row["agent_id"]
        vjson      = row["validated_output_json"]
        city       = row["city"] or ""
        market_date = row["market_date"] or ""

        by_rsid[rsid]["city"]         = city
        by_rsid[rsid]["market_date"]  = market_date

        # Extract agent_output from the validated_output_json envelope
        agent_output: dict = {}
        if isinstance(vjson, dict):
            agent_output = vjson.get("agent_output") or {}
        elif isinstance(vjson, str):
            try:
                parsed = json.loads(vjson)
                agent_output = parsed.get("agent_output") or {}
            except json.JSONDecodeError:
                pass

        # Native validation
        ns_passed, ns_reason = validate_subagent_output(agent_id, agent_output)
        if ns_passed:
            native_pass += 1
            by_rsid[rsid]["agents"][agent_id] = _FakeResult(success=True, tool_input=agent_output)
        else:
            native_fail += 1
            native_failures.append({
                "rsid":     rsid[:20] + "…",
                "agent_id": agent_id,
                "reason":   ns_reason,
            })
            # Failed native validation → treated as a missing/discarded result
            # for the canonical assembly step (do not populate with bad data)
            by_rsid[rsid]["agents"][agent_id] = _FakeResult(success=False, tool_input={})

    # ── Step 3+4+5: Canonical assembly + Step 9 validation, one per snapshot ──
    canonical_pass  = 0
    canonical_fail  = 0
    canonical_incomplete = 0
    canonical_failures: List[Dict] = []

    # Sentinel run_id for replay context
    replay_run_id = "replay-14c-offline"

    for rsid, snap_data in sorted(by_rsid.items()):
        city         = snap_data["city"]
        market_date  = snap_data["market_date"]
        agents       = snap_data["agents"]  # dict[agent_id, _FakeResult]

        n_complete = sum(1 for r in agents.values() if r.success)

        canonical_payload: Optional[dict] = None
        try:
            canonical_payload = _assemble_payload(city, market_date, replay_run_id, agents)
        except Exception as exc:
            val_status  = "INCOMPLETE"
            val_detail  = f"ASSEMBLY_ERROR: {exc}"
            canonical_incomplete += 1
            canonical_failures.append({"rsid": rsid[:20] + "…", "reason": val_detail})
            _upsert_validation_row(conn, rsid, None, val_status, val_detail)
            continue

        if n_complete < len(AGENT_IDS):
            val_status = "INCOMPLETE"
            missing = sorted(set(AGENT_IDS) - {aid for aid, r in agents.items() if r.success})
            val_detail = (
                f"{n_complete}/{len(AGENT_IDS)} agents had valid native outputs; "
                f"missing or failed: {missing}"
            )
            canonical_incomplete += 1
            canonical_failures.append({"rsid": rsid[:20] + "…", "reason": val_detail})
        else:
            try:
                vr = validate_shadow_output(canonical_payload)
                if vr.passed:
                    val_status  = "SCHEMA_VALID"
                    val_detail  = None
                    canonical_pass += 1
                else:
                    val_status  = "SCHEMA_INVALID"
                    val_detail  = vr.reason
                    canonical_fail += 1
                    canonical_failures.append({"rsid": rsid[:20] + "…", "reason": val_detail})
            except Exception as exc:
                val_status  = "SCHEMA_INVALID"
                val_detail  = f"VALIDATION_EXCEPTION: {exc}"
                canonical_fail += 1
                canonical_failures.append({"rsid": rsid[:20] + "…", "reason": val_detail})

        _upsert_validation_row(conn, rsid, canonical_payload, val_status, val_detail)

    conn.close()

    # ── Report ────────────────────────────────────────────────────────────────
    sep = "─" * 72

    print(f"\n{sep}")
    print("Step 14C — Offline Replay Report")
    print(sep)

    print(f"\n── NATIVE VALIDATION (125 rows, one per agent output) ──")
    print(f"  PASS : {native_pass:3d}")
    print(f"  FAIL : {native_fail:3d}")
    if native_failures:
        print(f"\n  Failures:")
        for f in native_failures:
            print(f"    [{f['agent_id']}] {f['rsid']}")
            print(f"      reason: {f['reason']}")
    else:
        print(f"  (no native failures)")

    print(f"\n── CANONICAL ASSEMBLY + STEP 9 VALIDATION (25 snapshots) ──")
    print(f"  SCHEMA_VALID   : {canonical_pass:3d}")
    print(f"  SCHEMA_INVALID : {canonical_fail:3d}")
    print(f"  INCOMPLETE     : {canonical_incomplete:3d}")
    if canonical_failures:
        print(f"\n  Failures / incomplete:")
        for f in canonical_failures:
            print(f"    {f['rsid']}")
            print(f"      reason: {f['reason']}")
    else:
        print(f"  (no canonical failures)")

    print(f"\n{sep}")
    print(f"Results upserted to kalshi_wx_shadow_snapshot_schema_validation.")
    print(sep)

    # Exit 1 if any failure so CI can detect it
    if native_fail > 0 or canonical_fail > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
