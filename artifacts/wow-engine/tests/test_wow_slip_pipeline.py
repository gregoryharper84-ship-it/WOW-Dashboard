"""Paper-trade slip pipeline: builder gates, archetypes, grader, JSONL store."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from wow_slip_builder import (
    ARCHETYPES,
    ArchetypeId,
    Direction,
    LegCandidate,
    Modifier,
    PowerProduct,
    SlipSnapshot,
    build_paper_slip,
)
from wow_slip_grader import LegResult, grade_slip
from wow_slip_store import SlipLedgerStore

CT = ZoneInfo("America/Chicago")
UTC = timezone.utc


def _ts(year, month, day, hour, minute, tz=CT) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=tz)


def _leg(
    *,
    leg_id: str,
    game_id: str,
    market: str,
    direction: Direction,
    line: float = 10.5,
    lower: float = 0.60,
    prob: float | None = None,
    modifier: Modifier = Modifier.NORMAL,
    event_start: datetime | None = None,
    active: bool = True,
    promo: bool = False,
    player_id: str | None = None,
) -> dict:
    return {
        "leg_id": leg_id,
        "player_id": player_id or f"p-{leg_id}",
        "player_name": f"Player {leg_id}",
        "game_id": game_id,
        "market": market,
        "direction": direction,
        "line": line,
        "modifier": modifier,
        "calibrated_probability": prob if prob is not None else min(lower + 0.05, 0.99),
        "calibrated_lower_bound": lower,
        "event_start_time": event_start
        or datetime(2026, 11, 26, 1, 0, tzinfo=UTC),
        "is_confirmed_active": active,
        "is_promo_square": promo,
    }


class TestPregameAndIntegrityGates:
    def test_pregame_timestamp_violation_raises_validation_error(self):
        captured = datetime(2026, 11, 26, 2, 0, tzinfo=UTC)
        start = datetime(2026, 11, 26, 1, 0, tzinfo=UTC)  # already started
        legs = [
            _leg(
                leg_id=f"l{i}",
                game_id=f"g{i}",
                market="personal_fouls",
                direction=Direction.LESS,
                event_start=start,
            )
            for i in range(6)
        ]
        # Capture inside niche early window in CT.
        captured_ct = _ts(2026, 11, 26, 5, 0, CT)
        with pytest.raises(ValidationError):
            build_paper_slip(
                ArchetypeId.NBA_NICHE_UNDER_EARLY,
                legs,
                captured_at=captured_ct,
            )

    def test_can_execute_false_on_every_snapshot(self):
        captured = _ts(2026, 11, 26, 5, 0, CT)
        start = captured + timedelta(hours=12)
        legs = [
            _leg(
                leg_id=f"l{i}",
                game_id=f"g{i // 2}",
                market="personal_fouls",
                direction=Direction.LESS,
                event_start=start,
                lower=0.62 + i * 0.01,
            )
            for i in range(6)
        ]
        snap = build_paper_slip(
            ArchetypeId.NBA_NICHE_UNDER_EARLY, legs, captured_at=captured
        )
        assert snap.can_execute is False
        assert isinstance(snap, SlipSnapshot)
        # Assignment and re-validation both reject can_execute=True.
        with pytest.raises(ValidationError):
            snap.can_execute = True  # type: ignore[assignment]
        with pytest.raises(ValidationError):
            SlipSnapshot.model_validate(
                {**snap.model_dump(mode="json"), "can_execute": True}
            )


class TestFilteringGates:
    def test_goblin_legs_filtered_out(self):
        captured = _ts(2026, 11, 26, 5, 0, CT)
        start = captured + timedelta(hours=12)
        legs = [
            _leg(
                leg_id=f"l{i}",
                game_id=f"g{i}",
                market="personal_fouls",
                direction=Direction.LESS,
                event_start=start,
                lower=0.70 - i * 0.01,
            )
            for i in range(6)
        ]
        # Replace one high-ranked leg with goblin — must be skipped, not selected.
        legs[0]["modifier"] = Modifier.GOBLIN
        # Add a 7th eligible replacement so the slip can still form.
        legs.append(
            _leg(
                leg_id="l6",
                game_id="g6",
                market="blocked_shots",
                direction=Direction.LESS,
                event_start=start,
                lower=0.56,
            )
        )
        snap = build_paper_slip(
            ArchetypeId.NBA_NICHE_UNDER_EARLY, legs, captured_at=captured
        )
        assert all(leg.modifier != Modifier.GOBLIN for leg in snap.legs)
        assert "l0" not in {leg.leg_id for leg in snap.legs}

    def test_out_of_archetype_markets_filtered(self):
        captured = _ts(2026, 11, 26, 5, 0, CT)
        start = captured + timedelta(hours=12)
        legs = [
            _leg(
                leg_id=f"ok{i}",
                game_id=f"g{i}",
                market="personal_fouls",
                direction=Direction.LESS,
                event_start=start,
                lower=0.60,
            )
            for i in range(6)
        ]
        legs.append(
            _leg(
                leg_id="bad",
                game_id="gx",
                market="points",  # not in niche-under markets
                direction=Direction.LESS,
                event_start=start,
                lower=0.99,
            )
        )
        snap = build_paper_slip(
            ArchetypeId.NBA_NICHE_UNDER_EARLY, legs, captured_at=captured
        )
        assert "bad" not in {leg.leg_id for leg in snap.legs}
        assert all(leg.market in ARCHETYPES[ArchetypeId.NBA_NICHE_UNDER_EARLY].markets for leg in snap.legs)


class TestDirectionalLock:
    def test_allows_single_promo_square_exception(self):
        captured = _ts(2026, 11, 26, 5, 0, CT)
        start = captured + timedelta(hours=12)
        legs = [
            _leg(
                leg_id=f"l{i}",
                game_id=f"g{i}",
                market="turnovers",
                direction=Direction.LESS,
                event_start=start,
                lower=0.61,
            )
            for i in range(5)
        ]
        legs.append(
            _leg(
                leg_id="promo",
                game_id="g5",
                market="blocked_shots",
                direction=Direction.MORE,  # opposite of archetype LESS
                line=0.5,
                event_start=start,
                lower=0.70,
                promo=True,
            )
        )
        snap = build_paper_slip(
            ArchetypeId.NBA_NICHE_UNDER_EARLY, legs, captured_at=captured
        )
        exceptions = [leg for leg in snap.legs if leg.is_direction_exception]
        assert len(exceptions) == 1
        assert exceptions[0].leg_id == "promo"
        assert exceptions[0].is_promo_square is True

    def test_rejects_mixed_non_promo_directions(self):
        captured = _ts(2026, 11, 26, 5, 0, CT)
        start = captured + timedelta(hours=12)
        legs = [
            _leg(
                leg_id=f"l{i}",
                game_id=f"g{i}",
                market="personal_fouls",
                direction=Direction.LESS,
                event_start=start,
                lower=0.60,
            )
            for i in range(5)
        ]
        # Opposite direction without promo flag — filtered; slip must fail for short count.
        legs.append(
            _leg(
                leg_id="mixed",
                game_id="g5",
                market="blocked_shots",
                direction=Direction.MORE,
                event_start=start,
                lower=0.90,
                promo=False,
            )
        )
        with pytest.raises(ValidationError):
            build_paper_slip(
                ArchetypeId.NBA_NICHE_UNDER_EARLY, legs, captured_at=captured
            )


class TestArchetypeStructures:
    def test_nba_half_pace_fade_enforces_3x2_game_stack(self):
        captured = datetime(2026, 11, 26, 15, 0, tzinfo=UTC)  # no CT window on this arch
        start = captured + timedelta(hours=6)
        legs = []
        markets = ["pra_1h", "pra_2h", "fantasy_score_1h", "fantasy_score_2h"]
        for g in range(3):
            for j in range(2):
                legs.append(
                    _leg(
                        leg_id=f"g{g}-l{j}",
                        game_id=f"game{g}",
                        market=markets[j % len(markets)],
                        direction=Direction.LESS,
                        event_start=start,
                        lower=0.65 - g * 0.01 - j * 0.001,
                    )
                )
        # Extra legs on a 4th game must not be used to break the 3x2 stack.
        legs.append(
            _leg(
                leg_id="extra",
                game_id="game3",
                market="pra_1h",
                direction=Direction.LESS,
                event_start=start,
                lower=0.99,
            )
        )
        snap = build_paper_slip(
            ArchetypeId.NBA_HALF_PACE_FADE, legs, captured_at=captured
        )
        assert snap.product == PowerProduct.SIX_PICK
        assert snap.multiplier == 37.5
        counts: dict[str, int] = {}
        for leg in snap.legs:
            counts[leg.game_id] = counts.get(leg.game_id, 0) + 1
        assert len(counts) == 3
        assert set(counts.values()) == {2}
        assert "extra" not in {leg.leg_id for leg in snap.legs}

    def test_nba_half_pace_fade_rejects_incomplete_stack(self):
        captured = datetime(2026, 11, 26, 15, 0, tzinfo=UTC)
        start = captured + timedelta(hours=6)
        # 4 games with 1 leg each — cannot form 3x2.
        legs = [
            _leg(
                leg_id=f"l{i}",
                game_id=f"g{i}",
                market="pra_1h",
                direction=Direction.LESS,
                event_start=start,
                lower=0.60,
            )
            for i in range(6)
        ]
        with pytest.raises(ValidationError, match="3x2|game stack"):
            build_paper_slip(
                ArchetypeId.NBA_HALF_PACE_FADE, legs, captured_at=captured
            )

    def test_nba_4_pack_fade_four_distinct_games_10x(self):
        captured = _ts(2026, 11, 26, 18, 0, CT)
        start = captured + timedelta(hours=3)
        legs = [
            _leg(
                leg_id=f"l{i}",
                game_id=f"g{i}",
                market=["pra", "points", "fantasy_score", "pra"][i],
                direction=Direction.LESS,
                event_start=start,
                lower=0.58 + i * 0.02,
            )
            for i in range(4)
        ]
        snap = build_paper_slip(
            ArchetypeId.NBA_4_PACK_FADE, legs, captured_at=captured
        )
        assert snap.product == PowerProduct.FOUR_PICK
        assert snap.multiplier == 10.0
        assert len(snap.legs) == 4
        assert len({leg.game_id for leg in snap.legs}) == 4


class TestAdditionalArchetypesAndModifiers:
    def test_max_one_demon_per_slip(self):
        captured = _ts(2026, 11, 26, 5, 0, CT)
        start = captured + timedelta(hours=12)
        legs = [
            _leg(
                leg_id=f"l{i}",
                game_id=f"g{i}",
                market="personal_fouls",
                direction=Direction.LESS,
                event_start=start,
                lower=0.70 - i * 0.01,
                modifier=Modifier.DEMON if i < 2 else Modifier.NORMAL,
            )
            for i in range(6)
        ]
        # Extra normal leg so we still reach 6 after skipping second demon.
        legs.append(
            _leg(
                leg_id="l6",
                game_id="g6",
                market="blocked_shots",
                direction=Direction.LESS,
                event_start=start,
                lower=0.55,
            )
        )
        snap = build_paper_slip(
            ArchetypeId.NBA_NICHE_UNDER_EARLY, legs, captured_at=captured
        )
        assert sum(1 for leg in snap.legs if leg.modifier == Modifier.DEMON) == 1

    def test_nfl_1q_kicker_combo_5_pick_20x(self):
        captured = datetime(2026, 11, 26, 16, 0, tzinfo=UTC)
        start = captured + timedelta(hours=4)
        markets = [
            "fg_made_combo_1q",
            "qb_fantasy_score_1q",
            "pass_attempts_1q",
            "fg_made_combo_1q",
            "pass_attempts_1q",
        ]
        legs = [
            _leg(
                leg_id=f"n{i}",
                game_id=f"nfl{i}",
                market=markets[i],
                direction=Direction.MORE,
                event_start=start,
                lower=0.57 + i * 0.01,
            )
            for i in range(5)
        ]
        snap = build_paper_slip(
            ArchetypeId.NFL_1Q_KICKER_COMBO, legs, captured_at=captured
        )
        assert snap.product == PowerProduct.FIVE_PICK
        assert snap.multiplier == 20.0
        assert len(snap.legs) == 5


class TestGrader:
    def _six_pick_snapshot(self):
        captured = _ts(2026, 11, 26, 5, 0, CT)
        start = captured + timedelta(hours=12)
        legs = [
            _leg(
                leg_id=f"l{i}",
                game_id=f"g{i // 2}",
                market="personal_fouls",
                direction=Direction.LESS,
                line=3.5,
                event_start=start,
                lower=0.60,
            )
            for i in range(6)
        ]
        return build_paper_slip(
            ArchetypeId.NBA_NICHE_UNDER_EARLY, legs, captured_at=captured
        )

    def test_dnp_push_downgrade_6_to_5(self):
        snap = self._six_pick_snapshot()
        # 5 wins (LESS: actual < line) + 1 DNP
        actuals = {
            "l0": 2.0,
            "l1": 1.0,
            "l2": 0.0,
            "l3": 2.0,
            "l4": 3.0,
            "l5": None,  # DNP
        }
        grade = grade_slip(snap, actuals)
        assert grade.pushes == 1
        assert grade.wins == 5
        assert grade.losses == 0
        assert grade.active_legs == 5
        assert grade.effective_multiplier == 20.0
        assert grade.net_units == 19.0
        assert grade.can_execute is False

    def test_push_on_exact_line(self):
        snap = self._six_pick_snapshot()
        actuals = {f"l{i}": 2.0 for i in range(5)}
        actuals["l5"] = 3.5  # exact line → push
        grade = grade_slip(snap, actuals)
        assert grade.pushes == 1
        assert grade.effective_multiplier == 20.0

    def test_fragile_win_when_margin_le_half(self):
        snap = self._six_pick_snapshot()
        # LESS line 3.5: actual 3.0 → margin 0.5 → fragile WIN
        actuals = {
            "l0": 3.0,
            "l1": 2.0,
            "l2": 1.0,
            "l3": 0.0,
            "l4": 2.5,
            "l5": 1.5,
        }
        grade = grade_slip(snap, actuals)
        fragile = [g for g in grade.graded_legs if g.is_fragile_win]
        assert any(g.leg_id == "l0" and g.signed_margin == 0.5 for g in fragile)
        assert all(g.result == LegResult.WIN for g in fragile)

    def test_loss_zeros_slip(self):
        snap = self._six_pick_snapshot()
        actuals = {f"l{i}": 2.0 for i in range(5)}
        actuals["l5"] = 10.0  # LESS loss
        grade = grade_slip(snap, actuals)
        assert grade.losses == 1
        assert grade.effective_multiplier == 0.0
        assert grade.net_units == -1.0


class TestSlipLedgerStore:
    def test_jsonl_roundtrip_snapshots_and_grades(self, tmp_path: Path):
        captured = _ts(2026, 11, 26, 5, 0, CT)
        start = captured + timedelta(hours=12)
        legs = [
            _leg(
                leg_id=f"l{i}",
                game_id=f"g{i // 2}",
                market="turnovers",
                direction=Direction.LESS,
                event_start=start,
                lower=0.60,
            )
            for i in range(6)
        ]
        snap = build_paper_slip(
            ArchetypeId.NBA_NICHE_UNDER_EARLY, legs, captured_at=captured
        )
        grade = grade_slip(
            snap,
            {f"l{i}": 1.0 for i in range(6)},
        )

        store = SlipLedgerStore(data_dir=tmp_path)
        store.write_snapshot(snap)
        store.write_grade(grade)

        snaps = store.read_snapshots()
        grades = store.read_grades()
        assert len(snaps) == 1
        assert len(grades) == 1
        assert snaps[0]["slip_id"] == str(snap.slip_id)
        assert snaps[0]["can_execute"] is False
        assert grades[0]["slip_id"] == str(snap.slip_id)
        assert grades[0]["can_execute"] is False
        assert grades[0]["effective_multiplier"] == 37.5
        assert grades[0]["net_units"] == 36.5

    def test_optional_supabase_mirror_insert(self, tmp_path: Path):
        class _Table:
            def __init__(self):
                self.rows = []

            def insert(self, row):
                self.rows.append(row)
                return self

        class _Client:
            def __init__(self):
                self.tables = {"wow_slip_snapshots": _Table(), "wow_slip_grades": _Table()}

            def table(self, name):
                return self.tables[name]

        captured = _ts(2026, 11, 26, 5, 0, CT)
        start = captured + timedelta(hours=12)
        legs = [
            _leg(
                leg_id=f"l{i}",
                game_id=f"g{i // 2}",
                market="blocked_shots",
                direction=Direction.LESS,
                event_start=start,
                lower=0.60,
            )
            for i in range(6)
        ]
        snap = build_paper_slip(
            ArchetypeId.NBA_NICHE_UNDER_EARLY, legs, captured_at=captured
        )
        grade = grade_slip(snap, {f"l{i}": None for i in range(6)})
        client = _Client()
        store = SlipLedgerStore(data_dir=tmp_path, supabase_client=client)
        store.write_snapshot(snap)
        store.write_grade(grade)
        assert len(client.tables["wow_slip_snapshots"].rows) == 1
        assert client.tables["wow_slip_snapshots"].rows[0]["can_execute"] is False
        assert len(client.tables["wow_slip_grades"].rows) == 1
        assert "graded_legs_json" in client.tables["wow_slip_grades"].rows[0]


class TestMigrationContract:
    def test_sql_enforces_can_execute_false(self):
        root = Path(__file__).resolve().parents[1]
        sql_paths = [
            root / "migrations" / "20261125_wow_slip_ledger.sql",
            root.parents[1] / "supabase" / "migrations" / "20261125_wow_slip_ledger.sql",
        ]
        for path in sql_paths:
            text = path.read_text(encoding="utf-8")
            assert "check (can_execute = false)" in text
            assert "wow_slip_snapshots" in text
            assert "wow_slip_grades" in text
            assert "wow_archetype_performance" in text
            assert "clears_promotion_gate" in text
