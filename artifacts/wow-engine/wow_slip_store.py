"""
Append-only paper-trade slip ledger (JSONL + optional Supabase mirror).

Never enables live execution. can_execute remains False on every write.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from wow_slip_builder import SlipSnapshot
from wow_slip_grader import SlipGrade

DEFAULT_DATA_DIR = Path("data/wow_slips")
SNAPSHOTS_FILENAME = "snapshots.jsonl"
GRADES_FILENAME = "graded.jsonl"


class SupabaseTable(Protocol):
    def insert(self, row: dict[str, Any]) -> Any: ...


class SupabaseClient(Protocol):
    def table(self, name: str) -> SupabaseTable: ...


def _jsonable(model: SlipSnapshot | SlipGrade) -> dict[str, Any]:
    payload = model.model_dump(mode="json")
    # Belt-and-suspenders: never persist a truthy can_execute.
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

    def write_snapshot(self, snapshot: SlipSnapshot) -> dict[str, Any]:
        if snapshot.can_execute is not False:
            raise ValueError("refusing to persist snapshot with can_execute!=False")
        row = _jsonable(snapshot)
        self._append_jsonl(self.snapshots_path, row)
        if self._supabase is not None:
            mirror = {
                "slip_id": row["slip_id"],
                "archetype": row["archetype"],
                "product": row["product"],
                "multiplier": row["multiplier"],
                "primary_direction": row["primary_direction"],
                "captured_at": row["captured_at"],
                "built_at": row["built_at"],
                "stake_units": row["stake_units"],
                "lane": row.get("lane", "WOW_PROP_LANE"),
                "can_execute": False,
                "legs_json": row["legs"],
            }
            self._supabase.table("wow_slip_snapshots").insert(mirror)
        return row

    def write_grade(self, grade: SlipGrade) -> dict[str, Any]:
        if grade.can_execute is not False:
            raise ValueError("refusing to persist grade with can_execute!=False")
        row = _jsonable(grade)
        self._append_jsonl(self.grades_path, row)
        if self._supabase is not None:
            mirror = {
                "slip_id": row["slip_id"],
                "archetype": row["archetype"],
                "product": row["product"],
                "total_legs": row["total_legs"],
                "wins": row["wins"],
                "losses": row["losses"],
                "pushes": row["pushes"],
                "active_legs": row["active_legs"],
                "original_multiplier": row["original_multiplier"],
                "effective_multiplier": row["effective_multiplier"],
                "net_units": row["net_units"],
                "fragile_win_count": row["fragile_win_count"],
                "graded_at": row["graded_at"],
                "can_execute": False,
                "graded_legs_json": row["graded_legs"],
            }
            self._supabase.table("wow_slip_grades").insert(mirror)
        return row

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
