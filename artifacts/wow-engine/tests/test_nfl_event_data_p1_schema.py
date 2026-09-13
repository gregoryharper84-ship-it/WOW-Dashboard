from pathlib import Path

import pytest

from nfl_event_data_p1 import DATASET_INJURIES, SourceSchemaChanged, _scan_csv


CORE_INJURY_COLUMNS = [
    "season",
    "team",
    "week",
    "gsis_id",
    "position",
    "report_status",
    "practice_status",
]


def _write_csv(tmp_path: Path, columns: list[str]) -> Path:
    path = tmp_path / "injuries.csv"
    path.write_text(
        ",".join(columns) + "\n" + ",".join("x" for _ in columns) + "\n",
        encoding="utf-8",
    )
    return path


def test_injuries_accept_game_type_without_date_modified(tmp_path: Path) -> None:
    path = _write_csv(tmp_path, [*CORE_INJURY_COLUMNS, "game_type"])

    columns, row_count = _scan_csv(path, DATASET_INJURIES)

    assert "game_type" in columns
    assert "date_modified" not in columns
    assert row_count == 1


def test_injuries_accept_season_type_without_date_modified(tmp_path: Path) -> None:
    path = _write_csv(tmp_path, [*CORE_INJURY_COLUMNS, "season_type"])

    columns, row_count = _scan_csv(path, DATASET_INJURIES)

    assert "season_type" in columns
    assert "date_modified" not in columns
    assert row_count == 1


def test_injuries_fail_closed_without_season_discriminator_alias(tmp_path: Path) -> None:
    path = _write_csv(tmp_path, CORE_INJURY_COLUMNS)

    with pytest.raises(SourceSchemaChanged, match="game_type\\|season_type"):
        _scan_csv(path, DATASET_INJURIES)


def test_injuries_still_require_core_identity_and_status_fields(tmp_path: Path) -> None:
    columns = [column for column in CORE_INJURY_COLUMNS if column != "team"]
    path = _write_csv(tmp_path, [*columns, "game_type"])

    with pytest.raises(SourceSchemaChanged, match="missing required columns: team"):
        _scan_csv(path, DATASET_INJURIES)
