"""
Append-only paper-trade slip ledger (JSONL + optional Supabase mirror).

Never enables live execution. can_execute remains False on every write.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from wow_slip_builder import SlipSnapshot
from wow_slip_grader import GradedSlip, SlipGrade

DEFAULT_DATA_DIR = Path("data/wow_slips")
SNAPSHOTS_FILENAME = "snapshots.jsonl"
GRADES_FILENAME = "graded.jsonl"


class InsertBuilder(Protocol):
    def execute(self) -> Any: ...


class SupabaseTable(Protocol):
    def insert(self, row: dict[str, Any]) -> InsertBuilder: ...


class SupabaseClient(Protocol):
    def table(self, name: str) -> SupabaseTable: ...


def _jsonable(model: SlipSnapshot | GradedSlip | SlipGrade) -> dict[str, Any]:
    payload = model.model_dump(mode="json")
    payload["can_execute"] = False
    return payload


class SlipLedgerStore:
    """Append-only JSONL ledger with optional Supabase mirror inserts."""

    def __init__(
        self,
        data_dir: str | Path = DEFAULT_DATA_DIR,
        supabase_client: SupabaseClient | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_path = self.data_dir / SNAPSHOTS_FILENAME
        self.grades_path = self.data_dir / GRADES_FILENAME
        self._supabase = supabase_client

    def _append_jsonl(self, path: Path, row: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")))
            handle.write("\n")

    def _mirror_insert(self, table: str, row: dict[str, Any]) -> None:
        if self._supabase is None:
            return
        self._supabase.table(table).insert(row).execute()

    def write_snapshot(self, snapshot: SlipSnapshot) -> dict[str, Any]:
        if snapshot.can_execute is not False:
            raise ValueError("refusing to persist snapshot with can_execute!=False")
        row = _jsonable(snapshot)
        self._append_jsonl(self.snapshots_path, row)
        mirror = {
            "slip_id": row["slip_id"],
            "platform": row["platform"],
            "archetype": row["archetype"],
            "slip_type": row["slip_type"],
            "payout_multiplier": row["payout_multiplier"],
            "recommended_unit_scale": row["recommended_unit_scale"],
            "joint_calibrated_prob": row["joint_calibrated_prob"],
            "expected_value_roi": row["expected_value_roi"],
            "min_leg_lower_bound": row["min_leg_lower_bound"],
            "unprotected_leg_count": row["unprotected_leg_count"],
            "signal_sources": row.get("signal_sources") or [],
            "can_execute": False,
            "legs_json": row["legs"],
        }
        self._mirror_insert("wow_slip_snapshots", mirror)
        return row

    def write_grade(self, grade: GradedSlip | SlipGrade) -> dict[str, Any]:
        if grade.can_execute is not False:
            raise ValueError("refusing to persist grade with can_execute!=False")
        row = _jsonable(grade)
        self._append_jsonl(self.grades_path, row)
        mirror = {
            "slip_id": row["slip_id"],
            "platform": row["platform"],
            "archetype": row["archetype"],
            "legs_won": row["legs_won"],
            "legs_lost": row["legs_lost"],
            "legs_pushed": row["legs_pushed"],
            "effective_multiplier": row["effective_multiplier"],
            "realized_net_units": row["realized_net_units"],
            "hold_to_settlement_net_units": row["hold_to_settlement_net_units"],
            "exit_alpha_units": row["exit_alpha_units"],
            "exit_action": row.get("exit_action"),
            "fragile_win_count": row["fragile_win_count"],
            "graded_legs_json": row["graded_legs"],
        }
        self._mirror_insert("wow_slip_grades", mirror)
        return row

    def count_snapshots_for_archetype_today(
        self,
        archetype: str,
        *,
        day_utc: str | None = None,
    ) -> int:
        """Helper for core_2p max_slips_per_day tracking."""
        from datetime import datetime, timezone

        if day_utc is None:
            day_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        count = 0
        for row in self.read_snapshots():
            if row.get("archetype") != archetype:
                continue
            created = str(row.get("created_at") or "")
            if created.startswith(day_utc):
                count += 1
        return count

    def read_snapshots(self) -> list[dict[str, Any]]:
        return self._read_jsonl(self.snapshots_path)

    def read_grades(self) -> list[dict[str, Any]]:
        return self._read_jsonl(self.grades_path)

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                rows.append(json.loads(line))
        return rows


__all__ = [
    "DEFAULT_DATA_DIR",
    "GRADES_FILENAME",
    "SNAPSHOTS_FILENAME",
    "SlipLedgerStore",
]
