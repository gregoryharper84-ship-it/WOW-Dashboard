"""Unified paper-trade slip pipeline: gates, archetypes, exit, grader, store."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from wow_kalshi_exit_engine import LiveSlipState, evaluate_kalshi_exit
from wow_slip_builder import (
    ARCHETYPES,
    ArchetypeId,
    Direction,
    MatchupTag,
    Modifier,
    PropLegCandidate,
    SlipSnapshot,
    SlipType,
    build_paper_slip,
    rank_score,
    signal_source_allowed,
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
    event_id: str,
    market: str,
    direction: Direction,
    line: float = 10.5,
    opening_line: float | None = None,
    lower: float = 0.60,
    prob: float | None = None,
    modifier: Modifier = Modifier.STANDARD,
    event_start: datetime | None = None,
    captured_at: datetime | None = None,
    active: bool = True,
    promo: bool = False,
    protected: bool = True,
    player: str | None = None,
    platform: str = "prizepicks",
    sport: str = "NBA",
    signal_source: str = "wow_prop_lane",
    matchup_tags: list[MatchupTag] | None = None,
    team: str = "TEAM",
    projected_mean: float | None = None,
    projected_std: float = 1.0,
) -> dict:
    start = event_start or datetime(2026, 11, 26, 23, 0, tzinfo=UTC)
    captured = captured_at or (start - timedelta(hours=6))
    return {
        "leg_id": leg_id,
        "platform": platform,
        "sport": sport,
        "event_id": event_id,
        "team": team,
        "player_or_combo": player or f"p-{leg_id}",
        "market": market,
        "line": line,
        "opening_line": opening_line,
        "direction": direction,
        "modifier": modifier,
        "is_promo_square": promo,
        "is_protected": protected,
        "signal_source": signal_source,
        "matchup_tags": matchup_tags or [],
        "calibrated_probability": prob if prob is not None else min(lower + 0.05, 0.99),
        "calibrated_lower_bound": lower,
        "projected_mean": projected_mean if projected_mean is not None else line,
        "projected_std": projected_std,
        "is_confirmed_active": active,
        "captured_at": captured,
        "event_start_time": start,
    }


# ---------------------------------------------------------------------------
# 1. Integrity & dry-run
# ---------------------------------------------------------------------------


class TestIntegrityAndDryRun:
    def test_pregame_timestamp_violation_raises_validation_error(self):
        start = datetime(2026, 11, 26, 1, 0, tzinfo=UTC)
        captured = datetime(2026, 11, 26, 2, 0, tzinfo=UTC)  # after start
        with pytest.raises(ValidationError):
            PropLegCandidate.model_validate(
                _leg(
                    leg_id="l0",
                    event_id="g0",
                    market="personal_fouls",
                    direction=Direction.LESS,
                    event_start=start,
                    captured_at=captured,
                )
            )

    def test_can_execute_false_on_every_snapshot(self):
        captured = _ts(2026, 11, 26, 5, 0, CT)
        start = captured + timedelta(hours=12)
        legs = [
            _leg(
                leg_id=f"l{i}",
                event_id=f"g{i // 2}",
                market="personal_fouls",
                direction=Direction.LESS,
                event_start=start,
                captured_at=captured,
                lower=0.62 + i * 0.01,
            )
            for i in range(6)
        ]
        snap = build_paper_slip(
            ArchetypeId.NBA_NICHE_UNDER_EARLY, legs, captured_at=captured
        )
        assert snap.can_execute is False
        assert isinstance(snap, SlipSnapshot)
        with pytest.raises(ValidationError):
            snap.can_execute = True  # type: ignore[assignment]
        with pytest.raises(ValidationError):
            SlipSnapshot.model_validate(
                {**snap.model_dump(mode="json"), "can_execute": True}
            )


# ---------------------------------------------------------------------------
# 2. Source governance
# ---------------------------------------------------------------------------


class TestSourceGovernance:
    def test_blocked_sources_unconditional(self):
        captured = _ts(2026, 11, 26, 5, 0, CT)
        start = captured + timedelta(hours=12)
        for source in ("huddle_cush", "huddle_crelyks", "huddle_nucks"):
            legs = [
                _leg(
                    leg_id=f"l{i}",
                    event_id=f"g{i}",
                    market="personal_fouls",
                    direction=Direction.LESS,
                    event_start=start,
                    captured_at=captured,
                    signal_source=source if i == 0 else "wow_prop_lane",
                    lower=0.70 - i * 0.01,
                )
                for i in range(7)
            ]
            snap = build_paper_slip(
                ArchetypeId.NBA_NICHE_UNDER_EARLY, legs, captured_at=captured
            )
            assert "l0" not in {leg.leg_id for leg in snap.legs}

    def test_huddle_hydro_cs2_ok_nba_rejected(self):
        captured = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
        start = captured + timedelta(hours=6)
        cs2_ok = PropLegCandidate.model_validate(
            _leg(
                leg_id="cs2",
                event_id="e1",
                market="cs2_maps_1_2_kills",
                direction=Direction.LESS,
                sport="CS2",
                signal_source="huddle_hydro",
                event_start=start,
                captured_at=captured,
            )
        )
        nba_bad = PropLegCandidate.model_validate(
            _leg(
                leg_id="nba",
                event_id="e2",
                market="personal_fouls",
                direction=Direction.LESS,
                sport="NBA",
                signal_source="huddle_hydro",
                event_start=start,
                captured_at=captured,
            )
        )
        assert signal_source_allowed(
            cs2_ok, archetype=ArchetypeId.CS2_KILLS_UNDER_POWER
        )
        assert not signal_source_allowed(
            nba_bad, archetype=ArchetypeId.NBA_NICHE_UNDER_EARLY
        )

    def test_huddle_goblin_soccer_ok_nba_kalshi_rejected(self):
        captured = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
        start = captured + timedelta(hours=6)
        soccer = PropLegCandidate.model_validate(
            _leg(
                leg_id="soc",
                event_id="m1",
                market="passes_attempted",
                direction=Direction.MORE,
                sport="SOCCER",
                signal_source="huddle_goblin",
                event_start=start,
                captured_at=captured,
            )
        )
        nba = PropLegCandidate.model_validate(
            _leg(
                leg_id="nba",
                event_id="g1",
                market="points",
                direction=Direction.MORE,
                sport="NBA",
                signal_source="huddle_goblin",
                event_start=start,
                captured_at=captured,
            )
        )
        kalshi = PropLegCandidate.model_validate(
            _leg(
                leg_id="k1",
                event_id="g1",
                market="nba_rebounds",
                direction=Direction.MORE,
                sport="NBA",
                platform="kalshi",
                signal_source="huddle_goblin",
                event_start=start,
                captured_at=captured,
            )
        )
        assert signal_source_allowed(
            soccer, archetype=ArchetypeId.SOCCER_POSSESSION_CORE_2P
        )
        assert not signal_source_allowed(
            nba, archetype=ArchetypeId.NBA_NICHE_UNDER_EARLY
        )
        assert not signal_source_allowed(
            kalshi, archetype=ArchetypeId.KALSHI_NBA_BOARDS_DIMES_SGP
        )


# ---------------------------------------------------------------------------
# 3. Bump decay
# ---------------------------------------------------------------------------


class TestBumpDecay:
    def test_moderate_adverse_bump_scales_units(self):
        captured = _ts(2026, 11, 26, 5, 0, CT)
        start = captured + timedelta(hours=12)
        legs = [
            _leg(
                leg_id=f"l{i}",
                event_id=f"g{i // 2}",
                market="personal_fouls",
                direction=Direction.LESS,
                line=4.5,
                opening_line=5.0 if i == 0 else None,  # adverse 0.5 on LESS
                event_start=start,
                captured_at=captured,
                lower=0.62,
            )
            for i in range(6)
        ]
        snap = build_paper_slip(
            ArchetypeId.NBA_NICHE_UNDER_EARLY, legs, captured_at=captured
        )
        assert snap.recommended_unit_scale == 0.65

    def test_heavy_steam_filters_leg(self):
        captured = _ts(2026, 11, 26, 5, 0, CT)
        start = captured + timedelta(hours=12)
        legs = [
            _leg(
                leg_id=f"l{i}",
                event_id=f"g{i}",
                market="personal_fouls",
                direction=Direction.LESS,
                line=3.5,
                opening_line=5.0 if i == 0 else None,  # adverse 1.5 >= 1.0
                event_start=start,
                captured_at=captured,
                lower=0.90 if i == 0 else 0.60,
            )
            for i in range(7)
        ]
        snap = build_paper_slip(
            ArchetypeId.NBA_NICHE_UNDER_EARLY, legs, captured_at=captured
        )
        assert "l0" not in {leg.leg_id for leg in snap.legs}

    def test_passes_attempted_hard_skip_at_2(self):
        captured = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
        start = captured + timedelta(hours=6)
        steamed = PropLegCandidate.model_validate(
            _leg(
                leg_id="pass",
                event_id="m1",
                market="passes_attempted",
                direction=Direction.MORE,
                line=62.5,
                opening_line=60.0,  # adverse 2.5 >= 2.0
                sport="SOCCER",
                event_start=start,
                captured_at=captured,
            )
        )
        from wow_slip_builder import passes_bump_decay_gate

        assert passes_bump_decay_gate(steamed) is False


# ---------------------------------------------------------------------------
# 4. PrizePicks gates
# ---------------------------------------------------------------------------


class TestPrizePicksGates:
    def test_goblin_blocked_on_nba(self):
        captured = _ts(2026, 11, 26, 5, 0, CT)
        start = captured + timedelta(hours=12)
        legs = [
            _leg(
                leg_id=f"l{i}",
                event_id=f"g{i}",
                market="personal_fouls",
                direction=Direction.LESS,
                event_start=start,
                captured_at=captured,
                modifier=Modifier.GOBLIN if i == 0 else Modifier.STANDARD,
                lower=0.70 - i * 0.01,
            )
            for i in range(7)
        ]
        snap = build_paper_slip(
            ArchetypeId.NBA_NICHE_UNDER_EARLY, legs, captured_at=captured
        )
        assert all(leg.modifier != Modifier.GOBLIN for leg in snap.legs)

    def test_soccer_core_allows_one_correlated_goblin(self):
        captured = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
        start = captured + timedelta(hours=6)
        legs = [
            _leg(
                leg_id="g1",
                event_id="match1",
                market="passes_attempted",
                direction=Direction.MORE,
                sport="SOCCER",
                modifier=Modifier.GOBLIN,
                event_start=start,
                captured_at=captured,
                lower=0.62,
            ),
            _leg(
                leg_id="s1",
                event_id="match1",
                market="shots",
                direction=Direction.MORE,
                sport="SOCCER",
                modifier=Modifier.STANDARD,
                event_start=start,
                captured_at=captured,
                lower=0.61,
            ),
        ]
        snap = build_paper_slip(
            ArchetypeId.SOCCER_POSSESSION_CORE_2P, legs, captured_at=captured
        )
        assert snap.payout_multiplier == 2.0
        assert sum(1 for leg in snap.legs if leg.modifier == Modifier.GOBLIN) == 1

    def test_soccer_flex_bans_goblin_and_allows_mixed_direction(self):
        captured = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
        start = captured + timedelta(hours=6)
        markets = [
            "passes_attempted",
            "goalie_saves",
            "shots",
            "shots_on_target",
            "fantasy_score",
            "passes_attempted",
        ]
        dirs = [
            Direction.MORE,
            Direction.LESS,
            Direction.MORE,
            Direction.LESS,
            Direction.MORE,
            Direction.LESS,
        ]
        legs = [
            _leg(
                leg_id=f"l{i}",
                event_id="m1" if i < 3 else "m2",
                market=markets[i],
                direction=dirs[i],
                sport="SOCCER",
                event_start=start,
                captured_at=captured,
                lower=0.60,
            )
            for i in range(6)
        ]
        # Goblin candidate should be ignored if added.
        legs.append(
            _leg(
                leg_id="gob",
                event_id="m1",
                market="shots",
                direction=Direction.MORE,
                sport="SOCCER",
                modifier=Modifier.GOBLIN,
                event_start=start,
                captured_at=captured,
                lower=0.99,
            )
        )
        snap = build_paper_slip(
            ArchetypeId.SOCCER_POSSESSION_STACK_FLEX, legs, captured_at=captured
        )
        assert snap.slip_type == SlipType.SIX_PICK_FLEX
        assert all(leg.modifier != Modifier.GOBLIN for leg in snap.legs)
        assert {leg.direction for leg in snap.legs} == {Direction.MORE, Direction.LESS}

    def test_directional_lock_single_promo_exception(self):
        captured = _ts(2026, 11, 26, 5, 0, CT)
        start = captured + timedelta(hours=12)
        legs = [
            _leg(
                leg_id=f"l{i}",
                event_id=f"g{i}",
                market="turnovers",
                direction=Direction.LESS,
                event_start=start,
                captured_at=captured,
                lower=0.61,
            )
            for i in range(5)
        ]
        legs.append(
            _leg(
                leg_id="promo",
                event_id="g5",
                market="blocked_shots",
                direction=Direction.MORE,
                promo=True,
                event_start=start,
                captured_at=captured,
                lower=0.70,
            )
        )
        snap = build_paper_slip(
            ArchetypeId.NBA_NICHE_UNDER_EARLY, legs, captured_at=captured
        )
        exceptions = [leg for leg in snap.legs if leg.is_direction_exception]
        assert len(exceptions) == 1
        assert exceptions[0].leg_id == "promo"

    def test_half_pace_3x2_stack(self):
        captured = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
        start = captured + timedelta(hours=6)
        legs = []
        for g in range(3):
            for j in range(2):
                legs.append(
                    _leg(
                        leg_id=f"g{g}_{j}",
                        event_id=f"game{g}",
                        market="pra_1h" if j == 0 else "pra_2h",
                        direction=Direction.LESS,
                        event_start=start,
                        captured_at=captured,
                        lower=0.60,
                    )
                )
        snap = build_paper_slip(
            ArchetypeId.NBA_HALF_PACE_FADE, legs, captured_at=captured
        )
        assert len(snap.legs) == 6
        by_game = {}
        for leg in snap.legs:
            by_game[leg.event_id] = by_game.get(leg.event_id, 0) + 1
        assert by_game == {"game0": 2, "game1": 2, "game2": 2}

    def test_nba_4_pack_one_per_game(self):
        captured = _ts(2026, 11, 26, 18, 0, CT)
        start = captured + timedelta(hours=4)
        legs = [
            _leg(
                leg_id=f"l{i}",
                event_id=f"g{i}",
                market="pra",
                direction=Direction.LESS,
                event_start=start,
                captured_at=captured,
                lower=0.60,
            )
            for i in range(4)
        ]
        snap = build_paper_slip(
            ArchetypeId.NBA_4_PACK_FADE, legs, captured_at=captured
        )
        assert snap.payout_multiplier == 10.0
        assert len({leg.event_id for leg in snap.legs}) == 4

    def test_micro_volume_line_caps(self):
        captured = _ts(2026, 11, 26, 11, 0, CT)
        start = captured + timedelta(hours=6)
        legs = [
            _leg(
                leg_id=f"ok{i}",
                event_id=f"g{i // 2}",
                market="field_goals_attempted",
                direction=Direction.MORE,
                line=12.5,
                event_start=start,
                captured_at=captured,
                lower=0.60,
            )
            for i in range(6)
        ]
        legs.append(
            _leg(
                leg_id="cap_ft",
                event_id="gx",
                market="free_throws_made",
                direction=Direction.MORE,
                line=2.5,  # > 1.5 capped out
                event_start=start,
                captured_at=captured,
                lower=0.99,
            )
        )
        snap = build_paper_slip(
            ArchetypeId.NBA_MICRO_VOLUME_OVER, legs, captured_at=captured
        )
        assert "cap_ft" not in {leg.leg_id for leg in snap.legs}

    def test_core_2p_lower_bound_bar(self):
        captured = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
        start = captured + timedelta(hours=6)
        weak = [
            _leg(
                leg_id="a",
                event_id="g1",
                market="pra",
                direction=Direction.MORE,
                event_start=start,
                captured_at=captured,
                lower=0.50,
            ),
            _leg(
                leg_id="b",
                event_id="g1",
                market="points",
                direction=Direction.LESS,
                event_start=start,
                captured_at=captured,
                lower=0.51,
            ),
        ]
        with pytest.raises(ValidationError):
            build_paper_slip(
                ArchetypeId.CORE_2P_VALUE_POWER, weak, captured_at=captured
            )
        strong = [
            _leg(
                leg_id="a",
                event_id="g1",
                market="pra",
                direction=Direction.MORE,
                event_start=start,
                captured_at=captured,
                lower=0.59,
            ),
            _leg(
                leg_id="b",
                event_id="g1",
                market="points",
                direction=Direction.LESS,
                event_start=start,
                captured_at=captured,
                lower=0.58,
            ),
        ]
        snap = build_paper_slip(
            ArchetypeId.CORE_2P_VALUE_POWER, strong, captured_at=captured
        )
        assert snap.payout_multiplier == 3.0
        assert snap.min_leg_lower_bound >= 0.58

    def test_cs2_requires_mostly_less(self):
        captured = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
        start = captured + timedelta(hours=4)
        legs = [
            _leg(
                leg_id=f"l{i}",
                event_id="match",
                market="cs2_maps_1_2_kills",
                direction=Direction.LESS,
                sport="CS2",
                event_start=start,
                captured_at=captured,
                lower=0.60,
            )
            for i in range(2)
        ]
        snap = build_paper_slip(
            ArchetypeId.CS2_KILLS_UNDER_POWER, legs, captured_at=captured
        )
        assert snap.slip_type == SlipType.TWO_PICK_POWER
        assert all(leg.direction == Direction.LESS for leg in snap.legs)


# ---------------------------------------------------------------------------
# 5. Kalshi gates
# ---------------------------------------------------------------------------


class TestKalshiGates:
    def _kalshi_leg(self, **kwargs):
        kwargs.setdefault("platform", "kalshi")
        return _leg(**kwargs)

    def test_nfl_qb_stack_accepted(self):
        captured = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
        start = captured + timedelta(hours=6)
        legs = [
            self._kalshi_leg(
                leg_id="qb",
                event_id="nfl1",
                market="nfl_pass_yards",
                direction=Direction.MORE,
                sport="NFL",
                event_start=start,
                captured_at=captured,
                lower=0.60,
            ),
            self._kalshi_leg(
                leg_id="rec",
                event_id="nfl1",
                market="nfl_rec_yards",
                direction=Direction.MORE,
                sport="NFL",
                event_start=start,
                captured_at=captured,
                lower=0.58,
            ),
        ]
        snap = build_paper_slip(
            ArchetypeId.KALSHI_NFL_QB_STACK_SGP,
            legs,
            captured_at=captured,
            payout_multiplier=3.5,
        )
        assert snap.slip_type == SlipType.KALSHI_SGP_2
        assert snap.can_execute is False
        assert snap.expected_value_roi == pytest.approx(
            snap.joint_calibrated_prob * 3.5 - 1.0
        )

    def test_same_player_double_cutter_putback(self):
        captured = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
        start = captured + timedelta(hours=6)
        legs = [
            self._kalshi_leg(
                leg_id="pts",
                event_id="nba1",
                market="nba_points",
                direction=Direction.MORE,
                sport="NBA",
                player="Player A",
                matchup_tags=[MatchupTag.CUTTER_PUTBACK_FLOOR],
                event_start=start,
                captured_at=captured,
            ),
            self._kalshi_leg(
                leg_id="reb",
                event_id="nba1",
                market="nba_rebounds",
                direction=Direction.MORE,
                sport="NBA",
                player="Player A",
                matchup_tags=[MatchupTag.CUTTER_PUTBACK_FLOOR],
                event_start=start,
                captured_at=captured,
            ),
        ]
        snap = build_paper_slip(
            ArchetypeId.KALSHI_SAME_PLAYER_DOUBLE_SGP,
            legs,
            captured_at=captured,
            payout_multiplier=2.8,
        )
        assert len(snap.legs) == 2

    def test_boards_dimes_accepted(self):
        captured = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
        start = captured + timedelta(hours=6)
        legs = [
            self._kalshi_leg(
                leg_id="r",
                event_id="nba1",
                market="nba_rebounds",
                direction=Direction.MORE,
                sport="NBA",
                event_start=start,
                captured_at=captured,
            ),
            self._kalshi_leg(
                leg_id="a",
                event_id="nba1",
                market="nba_assists",
                direction=Direction.MORE,
                sport="NBA",
                event_start=start,
                captured_at=captured,
            ),
        ]
        snap = build_paper_slip(
            ArchetypeId.KALSHI_NBA_BOARDS_DIMES_SGP,
            legs,
            captured_at=captured,
            payout_multiplier=2.2,
        )
        assert snap.platform == "kalshi"

    def test_rejects_singles_cross_game_banned_markets(self):
        captured = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
        start = captured + timedelta(hours=6)
        # Cross-game
        with pytest.raises(ValidationError):
            build_paper_slip(
                ArchetypeId.KALSHI_NFL_QB_STACK_SGP,
                [
                    self._kalshi_leg(
                        leg_id="qb",
                        event_id="g1",
                        market="nfl_pass_yards",
                        direction=Direction.MORE,
                        sport="NFL",
                        event_start=start,
                        captured_at=captured,
                    ),
                    self._kalshi_leg(
                        leg_id="rec",
                        event_id="g2",
                        market="nfl_rec_yards",
                        direction=Direction.MORE,
                        sport="NFL",
                        event_start=start,
                        captured_at=captured,
                    ),
                ],
                captured_at=captured,
                payout_multiplier=3.0,
            )
        # Banned rush yards
        with pytest.raises(ValidationError):
            build_paper_slip(
                ArchetypeId.KALSHI_NFL_QB_STACK_SGP,
                [
                    self._kalshi_leg(
                        leg_id="qb",
                        event_id="g1",
                        market="nfl_pass_yards",
                        direction=Direction.MORE,
                        sport="NFL",
                        event_start=start,
                        captured_at=captured,
                    ),
                    self._kalshi_leg(
                        leg_id="rush",
                        event_id="g1",
                        market="nfl_rush_yards",
                        direction=Direction.MORE,
                        sport="NFL",
                        event_start=start,
                        captured_at=captured,
                    ),
                ],
                captured_at=captured,
                payout_multiplier=3.0,
            )
        # NHL banned
        with pytest.raises(ValidationError):
            build_paper_slip(
                ArchetypeId.KALSHI_SAME_PLAYER_DOUBLE_SGP,
                [
                    self._kalshi_leg(
                        leg_id="a",
                        event_id="g1",
                        market="nba_assists",
                        direction=Direction.MORE,
                        sport="NHL",
                        player="X",
                        event_start=start,
                        captured_at=captured,
                    ),
                    self._kalshi_leg(
                        leg_id="b",
                        event_id="g1",
                        market="nba_rebounds",
                        direction=Direction.MORE,
                        sport="NHL",
                        player="X",
                        event_start=start,
                        captured_at=captured,
                    ),
                ],
                captured_at=captured,
                payout_multiplier=2.0,
            )
        # Standalone nba_points on boards/dimes even with tags
        with pytest.raises(ValidationError):
            build_paper_slip(
                ArchetypeId.KALSHI_NBA_BOARDS_DIMES_SGP,
                [
                    self._kalshi_leg(
                        leg_id="pts",
                        event_id="g1",
                        market="nba_points",
                        direction=Direction.MORE,
                        sport="NBA",
                        matchup_tags=[MatchupTag.CUTTER_PUTBACK_FLOOR],
                        event_start=start,
                        captured_at=captured,
                        lower=0.99,
                    ),
                    self._kalshi_leg(
                        leg_id="reb",
                        event_id="g1",
                        market="nba_rebounds",
                        direction=Direction.MORE,
                        sport="NBA",
                        event_start=start,
                        captured_at=captured,
                    ),
                ],
                captured_at=captured,
                payout_multiplier=2.0,
            )


# ---------------------------------------------------------------------------
# 6. Matchup tags
# ---------------------------------------------------------------------------


class TestMatchupTags:
    def test_reorders_without_mutating_probabilities(self):
        captured = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
        start = captured + timedelta(hours=6)
        plain = PropLegCandidate.model_validate(
            _leg(
                leg_id="plain",
                event_id="g1",
                market="nba_rebounds",
                direction=Direction.MORE,
                platform="kalshi",
                sport="NBA",
                lower=0.60,
                prob=0.65,
                event_start=start,
                captured_at=captured,
            )
        )
        tagged = PropLegCandidate.model_validate(
            _leg(
                leg_id="tagged",
                event_id="g1",
                market="nba_rebounds",
                direction=Direction.MORE,
                platform="kalshi",
                sport="NBA",
                lower=0.60,
                prob=0.65,
                matchup_tags=[MatchupTag.OPP_TRANSITION_LEAK],
                event_start=start,
                captured_at=captured,
            )
        )
        assert tagged.calibrated_probability == plain.calibrated_probability
        assert tagged.calibrated_lower_bound == plain.calibrated_lower_bound
        assert rank_score(tagged) > rank_score(plain)
        snap = build_paper_slip(
            ArchetypeId.KALSHI_NBA_BOARDS_DIMES_SGP,
            [
                plain.model_dump(mode="python"),
                tagged.model_dump(mode="python"),
                _leg(
                    leg_id="ast",
                    event_id="g1",
                    market="nba_assists",
                    direction=Direction.MORE,
                    platform="kalshi",
                    sport="NBA",
                    lower=0.55,
                    event_start=start,
                    captured_at=captured,
                ),
            ],
            captured_at=captured,
            payout_multiplier=2.5,
        )
        # Raw probs preserved on snapshot.
        for leg in snap.legs:
            if leg.leg_id == "tagged":
                assert leg.calibrated_probability == 0.65
                assert leg.calibrated_lower_bound == 0.60


# ---------------------------------------------------------------------------
# 7. Live-exit engine
# ---------------------------------------------------------------------------


class TestLiveExitEngine:
    def test_take_profit_near_certain(self):
        decision = evaluate_kalshi_exit(
            LiveSlipState(
                slip_id="s1",
                cost=10.0,
                current_bid=12.0,
                combo_live_prob=0.97,
                legs_settled_won=1,
                legs_remaining=1,
                remaining_leg_live_prob=0.90,
                quarter_or_period=3,
                seconds_remaining=400,
                score_diff=4,
            )
        )
        assert decision.action == "TAKE_PROFIT"
        assert decision.can_execute is False

    def test_take_profit_fragile_final_leg(self):
        decision = evaluate_kalshi_exit(
            LiveSlipState(
                slip_id="s1",
                cost=10.0,
                current_bid=12.0,
                combo_live_prob=0.70,
                legs_settled_won=1,
                legs_remaining=1,
                remaining_leg_live_prob=0.50,
                remaining_leg_market="nfl_rec_yards",
                quarter_or_period=3,
                seconds_remaining=500,
                score_diff=0,
            )
        )
        assert decision.action == "TAKE_PROFIT"

    def test_hold_anti_panic(self):
        decision = evaluate_kalshi_exit(
            LiveSlipState(
                slip_id="s1",
                cost=10.0,
                current_bid=8.0,
                combo_live_prob=0.45,
                legs_settled_won=0,
                legs_remaining=2,
                remaining_leg_live_prob=0.55,
                quarter_or_period=2,
                seconds_remaining=800,
                score_diff=-3,
            )
        )
        assert decision.action == "HOLD"
        assert "anti-panic" in decision.reason

    def test_cut_loss_injury(self):
        decision = evaluate_kalshi_exit(
            LiveSlipState(
                slip_id="s1",
                cost=10.0,
                current_bid=4.0,
                combo_live_prob=0.30,
                legs_settled_won=0,
                legs_remaining=2,
                remaining_leg_live_prob=0.40,
                quarter_or_period=2,
                seconds_remaining=600,
                score_diff=-10,
                has_injury_or_ejection=True,
            )
        )
        assert decision.action == "CUT_LOSS"

    def test_cut_loss_4q_collapse(self):
        decision = evaluate_kalshi_exit(
            LiveSlipState(
                slip_id="s1",
                cost=10.0,
                current_bid=2.0,
                combo_live_prob=0.15,
                legs_settled_won=0,
                legs_remaining=2,
                remaining_leg_live_prob=0.20,
                quarter_or_period=4,
                seconds_remaining=200,
                score_diff=-14,
            )
        )
        assert decision.action == "CUT_LOSS"


# ---------------------------------------------------------------------------
# 8. Grader & store
# ---------------------------------------------------------------------------


class TestGraderAndStore:
    def _nba_niche_snap(self):
        captured = _ts(2026, 11, 26, 5, 0, CT)
        start = captured + timedelta(hours=12)
        legs = [
            _leg(
                leg_id=f"l{i}",
                event_id=f"g{i // 2}",
                market="personal_fouls",
                direction=Direction.LESS,
                line=3.5,
                event_start=start,
                captured_at=captured,
                lower=0.60,
            )
            for i in range(6)
        ]
        return build_paper_slip(
            ArchetypeId.NBA_NICHE_UNDER_EARLY, legs, captured_at=captured
        )

    def test_power_dnp_downgrade_6_to_5(self):
        snap = self._nba_niche_snap()
        actuals = {f"l{i}": 1.0 for i in range(5)}
        actuals["l5"] = None  # DNP
        grade = grade_slip(snap, actuals)
        assert grade.legs_pushed == 1
        assert grade.legs_won == 5
        assert grade.effective_multiplier == 20.0
        assert grade.hold_to_settlement_net_units == 19.0

    def test_flex_payouts(self):
        captured = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
        start = captured + timedelta(hours=6)
        markets = [
            "passes_attempted",
            "goalie_saves",
            "shots",
            "shots_on_target",
            "fantasy_score",
            "passes_attempted",
        ]
        legs = [
            _leg(
                leg_id=f"l{i}",
                event_id="m1" if i < 3 else "m2",
                market=markets[i],
                direction=Direction.MORE if i % 2 == 0 else Direction.LESS,
                line=10.5,
                sport="SOCCER",
                event_start=start,
                captured_at=captured,
                lower=0.60,
            )
            for i in range(6)
        ]
        snap = build_paper_slip(
            ArchetypeId.SOCCER_POSSESSION_STACK_FLEX, legs, captured_at=captured
        )
        # 5/6 wins
        actuals = {}
        for i, leg in enumerate(snap.legs):
            if leg.direction == Direction.MORE:
                actuals[leg.leg_id] = leg.line + 2 if i < 5 else leg.line - 2
            else:
                actuals[leg.leg_id] = leg.line - 2 if i < 5 else leg.line + 2
        grade = grade_slip(snap, actuals)
        assert grade.legs_won == 5
        assert grade.effective_multiplier == 2.0

        # Force 4/6 by flipping two losses
        for i, leg in enumerate(snap.legs):
            if leg.direction == Direction.MORE:
                actuals[leg.leg_id] = leg.line + 2 if i < 4 else leg.line - 2
            else:
                actuals[leg.leg_id] = leg.line - 2 if i < 4 else leg.line + 2
        grade4 = grade_slip(snap, actuals)
        assert grade4.legs_won == 4
        assert grade4.effective_multiplier == 0.4

    def test_fragile_win_flag(self):
        snap = self._nba_niche_snap()
        actuals = {leg.leg_id: leg.line - 0.5 for leg in snap.legs}  # margin 0.5
        grade = grade_slip(snap, actuals)
        assert grade.fragile_win_count == 6
        assert all(g.is_fragile_win for g in grade.graded_legs)

    def test_kalshi_exit_alpha(self):
        captured = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
        start = captured + timedelta(hours=6)
        legs = [
            _leg(
                leg_id="qb",
                event_id="nfl1",
                market="nfl_pass_yards",
                direction=Direction.MORE,
                platform="kalshi",
                sport="NFL",
                line=250.5,
                event_start=start,
                captured_at=captured,
            ),
            _leg(
                leg_id="rec",
                event_id="nfl1",
                market="nfl_rec_yards",
                direction=Direction.MORE,
                platform="kalshi",
                sport="NFL",
                line=60.5,
                event_start=start,
                captured_at=captured,
            ),
        ]
        snap = build_paper_slip(
            ArchetypeId.KALSHI_NFL_QB_STACK_SGP,
            legs,
            captured_at=captured,
            payout_multiplier=3.0,
        )
        # Hold would win (+2.0), early exit at 1.5 => alpha -0.5
        win_actuals = {"qb": 300.0, "rec": 80.0}
        grade_pos = grade_slip(
            snap, win_actuals, early_exit_payout=2.5, exit_action="TAKE_PROFIT"
        )
        assert grade_pos.hold_to_settlement_net_units == pytest.approx(2.0)
        assert grade_pos.realized_net_units == pytest.approx(1.5)
        assert grade_pos.exit_alpha_units == pytest.approx(-0.5)

        # Hold would lose (-1), early exit at 0.4 => alpha +0.4? realized=-0.6, hold=-1, alpha=0.4
        loss_actuals = {"qb": 100.0, "rec": 10.0}
        grade_neg = grade_slip(
            snap, loss_actuals, early_exit_payout=0.4, exit_action="CUT_LOSS"
        )
        assert grade_neg.hold_to_settlement_net_units == pytest.approx(-1.0)
        assert grade_neg.exit_alpha_units == pytest.approx(0.4)

    def test_store_roundtrip_both_platforms(self, tmp_path: Path):
        class _Builder:
            def __init__(self):
                self.executed = False

            def execute(self):
                self.executed = True
                return {"ok": True}

        class _Table:
            def __init__(self):
                self.rows = []
                self.builders = []

            def insert(self, row):
                self.rows.append(row)
                b = _Builder()
                self.builders.append(b)
                return b

        class _Client:
            def __init__(self):
                self.tables = {
                    "wow_slip_snapshots": _Table(),
                    "wow_slip_grades": _Table(),
                }

            def table(self, name):
                return self.tables[name]

        client = _Client()
        store = SlipLedgerStore(data_dir=tmp_path, supabase_client=client)

        pp = self._nba_niche_snap()
        store.write_snapshot(pp)
        grade = grade_slip(pp, {leg.leg_id: leg.line - 1 for leg in pp.legs})
        store.write_grade(grade)

        captured = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
        start = captured + timedelta(hours=6)
        kalshi = build_paper_slip(
            ArchetypeId.KALSHI_NBA_BOARDS_DIMES_SGP,
            [
                _leg(
                    leg_id="r",
                    event_id="nba1",
                    market="nba_rebounds",
                    direction=Direction.MORE,
                    platform="kalshi",
                    sport="NBA",
                    event_start=start,
                    captured_at=captured,
                ),
                _leg(
                    leg_id="a",
                    event_id="nba1",
                    market="nba_assists",
                    direction=Direction.MORE,
                    platform="kalshi",
                    sport="NBA",
                    event_start=start,
                    captured_at=captured,
                ),
            ],
            captured_at=captured,
            payout_multiplier=2.4,
        )
        store.write_snapshot(kalshi)
        store.write_grade(
            grade_slip(kalshi, {"r": 20.0, "a": 10.0}, early_exit_payout=1.8)
        )

        snaps = store.read_snapshots()
        grades = store.read_grades()
        assert len(snaps) == 2
        assert len(grades) == 2
        assert all(s["can_execute"] is False for s in snaps)
        assert {s["platform"] for s in snaps} == {"prizepicks", "kalshi"}
        assert len(client.tables["wow_slip_snapshots"].rows) == 2
        assert all(
            r["can_execute"] is False
            for r in client.tables["wow_slip_snapshots"].rows
        )


class TestMigrationContract:
    def test_sql_enforces_can_execute_false_and_archetypes(self):
        root = Path(__file__).resolve().parents[1]
        sql_paths = [
            root / "migrations" / "20261126_wow_slip_ledger.sql",
            root.parents[1] / "supabase" / "migrations" / "20261126_wow_slip_ledger.sql",
        ]
        for path in sql_paths:
            text = path.read_text(encoding="utf-8")
            assert "check (can_execute = false)" in text
            assert "wow_slip_snapshots" in text
            assert "wow_slip_grades" in text
            assert "wow_archetype_performance" in text
            assert "clears_promotion_gate" in text
            assert "kalshi_nba_boards_dimes_sgp" in text
            assert "6_pick_flex" in text
            assert "total_exit_alpha_units" in text


class TestPropLaneAndIntelligenceCompat:
    def test_prop_lane_drops_uncertified_rows(self):
        from wow_slip_prop_lane import (
            build_paper_slip_from_prop_lane,
            eligible_candidates_from_prop_rows,
        )

        captured = _ts(2026, 11, 26, 5, 0, CT)
        start = captured + timedelta(hours=12)
        rows = []
        for i in range(6):
            rows.append(
                {
                    "leg_id": f"l{i}",
                    "player_id": f"p{i}",
                    "player_name": f"P{i}",
                    "game_id": f"g{i}",
                    "market": "personal_fouls",
                    "direction": "LESS",
                    "line": 3.5,
                    "calibrated_probability": 0.65,
                    "calibrated_lower_bound": 0.60,
                    "event_start_time": start,
                    "captured_at": captured,
                    "is_confirmed_active": True,
                    "certification_label": "CERTIFIED_FITTED",
                    "can_execute": False,
                    "sport": "NBA",
                    "platform": "prizepicks",
                }
            )
        rows.append(
            {
                "leg_id": "research",
                "player_id": "px",
                "player_name": "Research Only",
                "game_id": "gx",
                "market": "personal_fouls",
                "direction": "LESS",
                "line": 3.5,
                "calibrated_probability": 0.99,
                "calibrated_lower_bound": 0.98,
                "event_start_time": start,
                "captured_at": captured,
                "is_confirmed_active": True,
                "certification_label": "RESEARCH_ONLY",
                "can_execute": False,
                "sport": "NBA",
                "platform": "prizepicks",
            }
        )
        cands = eligible_candidates_from_prop_rows(rows)
        assert all(c.leg_id != "research" for c in cands)
        snap = build_paper_slip_from_prop_lane(
            ArchetypeId.NBA_NICHE_UNDER_EARLY, rows, captured_at=captured
        )
        assert snap.can_execute is False
        assert "research" not in {leg.leg_id for leg in snap.legs}

    def test_slip_intelligence_stream_separated(self):
        from wow_slip_intelligence import (
            assess_slip_exposure,
            record_slip_learning,
            streams_are_separated,
        )

        snap = TestGraderAndStore()._nba_niche_snap()
        exposure = assess_slip_exposure(snap)
        assert exposure.stream == "SLIP_INTELLIGENCE"
        assert exposure.can_execute is False
        grade = grade_slip(snap, {leg.leg_id: 1.0 for leg in snap.legs})
        learning = record_slip_learning(grade)
        assert learning.stream == "SLIP_INTELLIGENCE"
        assert learning.clears_promotion_gate is False
        sep = streams_are_separated()
        assert sep["auto_promote_archetypes"] is False
        assert sep["prediction_stream"] == "PREDICTION_INTELLIGENCE"

    def test_thirteen_archetypes_registered(self):
        assert len(ARCHETYPES) == 13
        assert len(ArchetypeId) == 13
