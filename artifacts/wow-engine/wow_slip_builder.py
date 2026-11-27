"""
WOW unified paper-trade slip builder (WOW_PROP_LANE).

Supports PrizePicks (10 archetypes) + Kalshi (3 archetypes) on one shared
architecture. Hard-locked dry-run only: every SlipSnapshot has can_execute=False.
No live execution, order placement, or capital allocation.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from enum import Enum
from itertools import combinations
from math import prod
from typing import Any, Literal, Optional, Sequence
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

CT = ZoneInfo("America/Chicago")

CAN_EXECUTE: Literal[False] = False
Platform = Literal["prizepicks", "kalshi"]
Sport = Literal["NBA", "NFL", "SOCCER", "CS2", "NHL", "NCAA"]

BLOCKED_SIGNAL_SOURCES = frozenset(
    {"huddle_cush", "huddle_crelyks", "huddle_nucks"}
)

DEFAULT_BUMP_HARD_SKIP = 1.0
PASSES_ATTEMPTED_BUMP_HARD_SKIP = 2.0
MODERATE_BUMP_UNIT_SCALE = 0.65

CORE_2P_MIN_LOWER_BOUND = 0.58
CORE_2P_MAX_SLIPS_PER_DAY = 3
CORE_2P_VOLUME_MARKETS = frozenset(
    {
        "pass_attempts",
        "free_throws_attempted",
        "receiving_yards",
        "rushing_yards",
        "pra",
    }
)

KALSHI_HARD_BANNED_MARKETS = frozenset(
    {
        "moneyline",
        "spread",
        "total",
        "nfl_rush_yards",
        "nba_threes",
    }
)
KALSHI_ALLOWED_SPORTS = frozenset({"NBA", "NFL"})

SAME_PLAYER_DOUBLE_PAIRS = frozenset(
    {
        frozenset({"nfl_pass_yards", "nfl_pass_tds"}),
        frozenset({"nfl_receptions", "nfl_rec_yards"}),
        frozenset({"nba_points", "nba_assists"}),
        frozenset({"nba_rebounds", "nba_assists"}),
        frozenset({"nba_points", "nba_rebounds"}),  # cutter_putback_floor gated
    }
)

DAFT_VIP_ELIGIBLE_ARCHETYPES = frozenset(
    {"kalshi_nba_boards_dimes_sgp", "kalshi_same_player_double_sgp"}
)
DAFT_VIP_MARKETS = frozenset({"nba_rebounds", "nba_assists", "nba_pra"})


class Direction(str, Enum):
    MORE = "MORE"
    LESS = "LESS"


class Modifier(str, Enum):
    STANDARD = "STANDARD"
    DEMON = "DEMON"
    GOBLIN = "GOBLIN"


class SlipType(str, Enum):
    TWO_PICK_POWER = "2_pick_power"
    THREE_PICK_POWER = "3_pick_power"
    FOUR_PICK_POWER = "4_pick_power"
    FIVE_PICK_POWER = "5_pick_power"
    SIX_PICK_POWER = "6_pick_power"
    SIX_PICK_FLEX = "6_pick_flex"
    KALSHI_SGP_2 = "kalshi_sgp_2"
    KALSHI_SGP_3 = "kalshi_sgp_3"


class MatchupTag(str, Enum):
    OPP_TRANSITION_LEAK = "opp_transition_leak"
    FRONTCOURT_INJURY_VACUUM = "frontcourt_injury_vacuum"
    PRIMARY_HUB_INJURY_BUMP = "primary_hub_injury_bump"
    HIGH_OPP_ASSISTED_FG_RATE = "high_opp_assisted_fg_rate"
    CUTTER_PUTBACK_FLOOR = "cutter_putback_floor"
    OVERNIGHT_SOFT_OPENER = "overnight_soft_opener"
    REPEAT_TARGET_HIT = "repeat_target_hit"
    POSSESSION_MISMATCH_FAVORITE = "possession_mismatch_favorite"
    UNDERDOG_KEEPER_VOLUME = "underdog_keeper_volume"


class ArchetypeId(str, Enum):
    NBA_NICHE_UNDER_EARLY = "nba_niche_under_early"
    NBA_4_PACK_FADE = "nba_4_pack_fade"
    NBA_HALF_PACE_FADE = "nba_half_pace_fade"
    NBA_MICRO_VOLUME_OVER = "nba_micro_volume_over"
    NFL_1Q_KICKER_COMBO = "nfl_1q_kicker_combo"
    NFL_NICHE_FADE = "nfl_niche_fade"
    CORE_2P_VALUE_POWER = "core_2p_value_power"
    SOCCER_POSSESSION_CORE_2P = "soccer_possession_core_2p"
    SOCCER_POSSESSION_STACK_FLEX = "soccer_possession_stack_flex"
    CS2_KILLS_UNDER_POWER = "cs2_kills_under_power"
    KALSHI_NFL_QB_STACK_SGP = "kalshi_nfl_qb_stack_sgp"
    KALSHI_SAME_PLAYER_DOUBLE_SGP = "kalshi_same_player_double_sgp"
    KALSHI_NBA_BOARDS_DIMES_SGP = "kalshi_nba_boards_dimes_sgp"


ALL_ARCHETYPE_IDS = frozenset(a.value for a in ArchetypeId)

POWER_MULTIPLIERS: dict[SlipType, float] = {
    SlipType.TWO_PICK_POWER: 3.0,
    SlipType.THREE_PICK_POWER: 6.0,
    SlipType.FOUR_PICK_POWER: 10.0,
    SlipType.FIVE_PICK_POWER: 20.0,
    SlipType.SIX_PICK_POWER: 37.5,
    SlipType.SIX_PICK_FLEX: 25.0,
}

TAG_MARKET_BOOSTS: dict[MatchupTag, frozenset[str]] = {
    MatchupTag.OPP_TRANSITION_LEAK: frozenset({"nba_rebounds"}),
    MatchupTag.FRONTCOURT_INJURY_VACUUM: frozenset({"nba_rebounds"}),
    MatchupTag.PRIMARY_HUB_INJURY_BUMP: frozenset({"nba_assists"}),
    MatchupTag.HIGH_OPP_ASSISTED_FG_RATE: frozenset({"nba_assists"}),
    MatchupTag.CUTTER_PUTBACK_FLOOR: frozenset(),
    MatchupTag.OVERNIGHT_SOFT_OPENER: frozenset(),
    MatchupTag.REPEAT_TARGET_HIT: frozenset(),
    MatchupTag.POSSESSION_MISMATCH_FAVORITE: frozenset(
        {"passes_attempted", "shots"}
    ),
    MatchupTag.UNDERDOG_KEEPER_VOLUME: frozenset(
        {"goalie_saves", "passes_attempted"}
    ),
}

SOCCER_MARKETS = frozenset(
    {
        "passes_attempted",
        "goalie_saves",
        "shots",
        "shots_on_target",
        "fantasy_score",
    }
)
CS2_MARKETS = frozenset(
    {
        "cs2_maps_1_2_kills",
        "cs2_map_1_kills",
        "cs2_maps_1_2_awp_kills",
        "cs2_maps_1_2_headshots",
    }
)


class CtWindow(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: time
    end: time

    def contains(self, captured_at: datetime) -> bool:
        local = captured_at.astimezone(CT).timetz().replace(tzinfo=None)
        return self.start <= local < self.end


class PropLegCandidate(BaseModel):
    """Eligible market row offered to the paper-trade builder."""

    model_config = ConfigDict(extra="forbid")

    leg_id: str
    platform: Platform
    sport: Sport
    event_id: str
    team: str
    player_or_combo: str
    market: str
    line: float
    opening_line: Optional[float] = None
    direction: Direction
    modifier: Modifier = Modifier.STANDARD
    is_promo_square: bool = False
    is_protected: bool = True
    signal_source: str = "wow_prop_lane"
    matchup_tags: list[MatchupTag] = Field(default_factory=list)
    calibrated_probability: float
    calibrated_lower_bound: float
    projected_mean: float
    projected_std: float
    is_confirmed_active: bool = True
    captured_at: datetime
    event_start_time: datetime

    @field_validator("calibrated_probability", "calibrated_lower_bound")
    @classmethod
    def _unit_interval(cls, value: float) -> float:
        if not (0.0 <= value <= 1.0):
            raise ValueError("probability fields must be in [0.0, 1.0]")
        return value

    @field_validator("projected_std")
    @classmethod
    def _positive_std(cls, value: float) -> float:
        if value <= 0.0:
            raise ValueError("projected_std must be > 0.0")
        return value

    @model_validator(mode="after")
    def _bounds_and_pregame(self) -> PropLegCandidate:
        if self.calibrated_lower_bound > self.calibrated_probability:
            raise ValueError(
                "calibrated_lower_bound must be <= calibrated_probability"
            )
        captured = _ensure_aware(self.captured_at)
        start = _ensure_aware(self.event_start_time)
        if not (captured < start):
            raise ValueError(
                "captured_at must be strictly before event_start_time "
                "(pregame integrity)"
            )
        # Normalize aware datetimes onto the model.
        object.__setattr__(self, "captured_at", captured)
        object.__setattr__(self, "event_start_time", start)
        return self


# Backward-compatible alias used by older prop-lane adapters/tests.
LegCandidate = PropLegCandidate


class SlipLeg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    leg_id: str
    platform: Platform
    sport: Sport
    event_id: str
    team: str
    player_or_combo: str
    market: str
    line: float
    opening_line: Optional[float] = None
    direction: Direction
    modifier: Modifier
    is_promo_square: bool = False
    is_protected: bool = True
    signal_source: str
    matchup_tags: list[MatchupTag] = Field(default_factory=list)
    calibrated_probability: float
    calibrated_lower_bound: float
    projected_mean: float
    projected_std: float
    is_confirmed_active: Literal[True] = True
    adverse_bump: float = 0.0
    is_direction_exception: bool = False
    # Compat mirrors for slip-intelligence concentration diagnostics.
    player_id: str = ""
    game_id: str = ""

    @model_validator(mode="after")
    def _fill_compat_ids(self) -> SlipLeg:
        if not self.player_id:
            object.__setattr__(self, "player_id", self.player_or_combo)
        if not self.game_id:
            object.__setattr__(self, "game_id", self.event_id)
        return self


class SlipSnapshot(BaseModel):
    """Immutable paper-trade slip snapshot. can_execute is permanently False."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    slip_id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    platform: Platform
    archetype: ArchetypeId
    slip_type: SlipType
    payout_multiplier: float
    recommended_unit_scale: float = 1.0
    joint_calibrated_prob: float
    expected_value_roi: float
    min_leg_lower_bound: float
    unprotected_leg_count: int = 0
    signal_sources: list[str] = Field(default_factory=list)
    legs: list[SlipLeg]
    lane: Literal["WOW_PROP_LANE"] = "WOW_PROP_LANE"
    can_execute: Literal[False] = False
    # Compat aliases for older callers/intelligence.
    product: Optional[SlipType] = None
    multiplier: Optional[float] = None
    primary_direction: Optional[Direction] = None
    captured_at: Optional[datetime] = None
    built_at: Optional[datetime] = None
    stake_units: float = 1.0

    @model_validator(mode="after")
    def _enforce_dry_run_and_aliases(self) -> SlipSnapshot:
        if self.can_execute is not False:
            raise ValueError("can_execute must be False (paper-trade only)")
        object.__setattr__(self, "product", self.slip_type)
        object.__setattr__(self, "multiplier", self.payout_multiplier)
        object.__setattr__(self, "built_at", self.created_at)
        if self.captured_at is None and self.legs:
            object.__setattr__(
                self,
                "captured_at",
                min(leg.event_start_time for leg in self.legs),
            )
        if self.primary_direction is None and self.legs:
            # Majority direction for diagnostics only.
            more = sum(1 for leg in self.legs if leg.direction == Direction.MORE)
            primary = (
                Direction.MORE
                if more > len(self.legs) / 2
                else Direction.LESS
            )
            object.__setattr__(self, "primary_direction", primary)
        return self


def _ensure_aware(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def _raise_slip_error(message: str, *, loc: tuple[str, ...] = ("legs",)) -> None:
    raise ValidationError.from_exception_data(
        "SlipSnapshot",
        [
            {
                "type": "value_error",
                "loc": loc,
                "input": None,
                "ctx": {"error": message},
            }
        ],
    )


def adverse_bump_for(leg: PropLegCandidate) -> float:
    if leg.opening_line is None:
        return 0.0
    if leg.direction == Direction.MORE:
        return max(0.0, float(leg.line) - float(leg.opening_line))
    return max(0.0, float(leg.opening_line) - float(leg.line))


def hard_skip_threshold(market: str) -> float:
    if market == "passes_attempted":
        return PASSES_ATTEMPTED_BUMP_HARD_SKIP
    return DEFAULT_BUMP_HARD_SKIP


def passes_bump_decay_gate(leg: PropLegCandidate) -> bool:
    bump = adverse_bump_for(leg)
    return bump < hard_skip_threshold(leg.market)


def signal_source_allowed(
    leg: PropLegCandidate,
    *,
    archetype: ArchetypeId,
) -> bool:
    source = (leg.signal_source or "").strip().lower()
    if source in BLOCKED_SIGNAL_SOURCES:
        return False
    if source == "huddle_hydro":
        return leg.platform == "prizepicks" and leg.sport == "CS2"
    if source == "huddle_goblin":
        return leg.platform == "prizepicks" and leg.sport == "SOCCER"
    if source == "daft_vip":
        if archetype.value not in DAFT_VIP_ELIGIBLE_ARCHETYPES:
            return False
        if leg.market in DAFT_VIP_MARKETS:
            return True
        return (
            leg.market == "nba_points"
            and MatchupTag.CUTTER_PUTBACK_FLOOR in leg.matchup_tags
        )
    # wow_prop_lane / lock_city_jayshmoke / huddle_cjaay / unknown: archetype rules.
    return True


def matching_tag_count(leg: PropLegCandidate) -> int:
    count = 0
    for tag in leg.matchup_tags:
        markets = TAG_MARKET_BOOSTS.get(tag, frozenset())
        if not markets:
            # Soft niche tags boost any market they appear on.
            if tag in {
                MatchupTag.OVERNIGHT_SOFT_OPENER,
                MatchupTag.REPEAT_TARGET_HIT,
            }:
                count += 1
            continue
        if leg.market in markets:
            # underdog_keeper_volume LESS passes_attempted is intentional.
            if (
                tag == MatchupTag.UNDERDOG_KEEPER_VOLUME
                and leg.market == "passes_attempted"
                and leg.direction != Direction.LESS
            ):
                continue
            count += 1
    return count


def rank_score(leg: PropLegCandidate) -> float:
    return float(leg.calibrated_lower_bound) + min(
        0.03, 0.015 * matching_tag_count(leg)
    )


def rank_key(leg: PropLegCandidate) -> tuple[int, float, float]:
    return (
        1 if leg.is_protected else 0,
        rank_score(leg),
        float(leg.calibrated_probability),
    )


def _to_slip_leg(
    leg: PropLegCandidate,
    *,
    primary: Optional[Direction] = None,
) -> SlipLeg:
    bump = adverse_bump_for(leg)
    is_exception = bool(primary is not None and leg.direction != primary)
    return SlipLeg(
        leg_id=leg.leg_id,
        platform=leg.platform,
        sport=leg.sport,
        event_id=leg.event_id,
        team=leg.team,
        player_or_combo=leg.player_or_combo,
        market=leg.market,
        line=leg.line,
        opening_line=leg.opening_line,
        direction=leg.direction,
        modifier=leg.modifier,
        is_promo_square=leg.is_promo_square,
        is_protected=leg.is_protected,
        signal_source=leg.signal_source,
        matchup_tags=list(leg.matchup_tags),
        calibrated_probability=leg.calibrated_probability,
        calibrated_lower_bound=leg.calibrated_lower_bound,
        projected_mean=leg.projected_mean,
        projected_std=leg.projected_std,
        is_confirmed_active=True,
        adverse_bump=bump,
        is_direction_exception=is_exception,
        player_id=leg.player_or_combo,
        game_id=leg.event_id,
    )


def _parse_candidate(raw: PropLegCandidate | dict[str, Any]) -> PropLegCandidate:
    if isinstance(raw, PropLegCandidate):
        return raw
    payload = dict(raw)
    # Compat shims from the earlier PrizePicks-only schema.
    if "player_or_combo" not in payload and "player_name" in payload:
        payload["player_or_combo"] = payload.pop("player_name")
    if "player_or_combo" not in payload and "player_id" in payload:
        payload["player_or_combo"] = payload["player_id"]
    if "event_id" not in payload and "game_id" in payload:
        payload["event_id"] = payload["game_id"]
    if "team" not in payload:
        payload["team"] = str(payload.get("team_abbr") or "UNK")
    if "platform" not in payload:
        payload["platform"] = "prizepicks"
    if "sport" not in payload:
        payload["sport"] = "NBA"
    if "projected_mean" not in payload:
        payload["projected_mean"] = float(payload.get("line", 0.0))
    if "projected_std" not in payload:
        payload["projected_std"] = 1.0
    if "captured_at" not in payload and "event_start_time" in payload:
        # Leave validation to fail if missing; callers should supply it.
        pass
    mod = payload.get("modifier")
    if isinstance(mod, str) and mod.upper() == "NORMAL":
        payload["modifier"] = Modifier.STANDARD
    return PropLegCandidate.model_validate(payload)


# ---------------------------------------------------------------------------
# Archetype market / structure specs
# ---------------------------------------------------------------------------


class ArchetypeSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    archetype_id: ArchetypeId
    platform: Platform
    slip_type: Optional[SlipType] = None  # fixed when known
    sports: frozenset[str]
    direction: Optional[Direction] = None  # None => mixed allowed
    markets: frozenset[str]
    max_legs_per_game: Optional[int] = None
    min_games: Optional[int] = None
    ct_window: Optional[CtWindow] = None
    exact_game_stack: Optional[tuple[int, int]] = None
    allow_goblin: bool = False
    flex_allowed: bool = False
    min_legs: int = 2
    max_legs: int = 6


ARCHETYPES: dict[ArchetypeId, ArchetypeSpec] = {
    ArchetypeId.NBA_NICHE_UNDER_EARLY: ArchetypeSpec(
        archetype_id=ArchetypeId.NBA_NICHE_UNDER_EARLY,
        platform="prizepicks",
        slip_type=SlipType.SIX_PICK_POWER,
        sports=frozenset({"NBA"}),
        direction=Direction.LESS,
        markets=frozenset(
            {
                "personal_fouls",
                "free_throws_attempted",
                "three_pointers_attempted",
                "blocks_plus_steals",
                "blocked_shots",
                "turnovers",
            }
        ),
        max_legs_per_game=2,
        min_games=3,
        ct_window=CtWindow(start=time(4, 30), end=time(6, 30)),
        min_legs=6,
        max_legs=6,
    ),
    ArchetypeId.NBA_4_PACK_FADE: ArchetypeSpec(
        archetype_id=ArchetypeId.NBA_4_PACK_FADE,
        platform="prizepicks",
        slip_type=SlipType.FOUR_PICK_POWER,
        sports=frozenset({"NBA"}),
        direction=Direction.LESS,
        markets=frozenset({"pra", "points", "fantasy_score"}),
        max_legs_per_game=1,
        min_games=4,
        ct_window=CtWindow(start=time(17, 30), end=time(18, 30)),
        min_legs=4,
        max_legs=4,
    ),
    ArchetypeId.NBA_HALF_PACE_FADE: ArchetypeSpec(
        archetype_id=ArchetypeId.NBA_HALF_PACE_FADE,
        platform="prizepicks",
        slip_type=SlipType.SIX_PICK_POWER,
        sports=frozenset({"NBA"}),
        direction=Direction.LESS,
        markets=frozenset(
            {"pra_1h", "pra_2h", "fantasy_score_1h", "fantasy_score_2h"}
        ),
        max_legs_per_game=2,
        exact_game_stack=(3, 2),
        min_legs=6,
        max_legs=6,
    ),
    ArchetypeId.NBA_MICRO_VOLUME_OVER: ArchetypeSpec(
        archetype_id=ArchetypeId.NBA_MICRO_VOLUME_OVER,
        platform="prizepicks",
        slip_type=SlipType.SIX_PICK_POWER,
        sports=frozenset({"NBA"}),
        direction=Direction.MORE,
        markets=frozenset(
            {
                "field_goals_attempted",
                "field_goals_made",
                "two_pointers_attempted",
                "two_pointers_made",
                "free_throws_made",
                "turnovers",
            }
        ),
        max_legs_per_game=2,
        ct_window=CtWindow(start=time(9, 30), end=time(14, 30)),
        min_legs=6,
        max_legs=6,
    ),
    ArchetypeId.NFL_1Q_KICKER_COMBO: ArchetypeSpec(
        archetype_id=ArchetypeId.NFL_1Q_KICKER_COMBO,
        platform="prizepicks",
        slip_type=SlipType.FIVE_PICK_POWER,
        sports=frozenset({"NFL"}),
        direction=Direction.MORE,
        markets=frozenset(
            {"fg_made_combo_1q", "qb_fantasy_score_1q", "pass_attempts_1q"}
        ),
        min_legs=5,
        max_legs=5,
    ),
    ArchetypeId.NFL_NICHE_FADE: ArchetypeSpec(
        archetype_id=ArchetypeId.NFL_NICHE_FADE,
        platform="prizepicks",
        slip_type=SlipType.SIX_PICK_POWER,
        sports=frozenset({"NFL"}),
        direction=Direction.LESS,
        markets=frozenset(
            {
                "completion_percentage",
                "longest_reception",
                "tackles_plus_assists",
                "receiving_yards",
                "passing_yards",
            }
        ),
        min_legs=6,
        max_legs=6,
    ),
    ArchetypeId.CORE_2P_VALUE_POWER: ArchetypeSpec(
        archetype_id=ArchetypeId.CORE_2P_VALUE_POWER,
        platform="prizepicks",
        slip_type=SlipType.TWO_PICK_POWER,
        sports=frozenset({"NBA", "NFL"}),
        direction=None,
        markets=frozenset(),  # open within sport rules
        max_legs_per_game=2,
        min_legs=2,
        max_legs=2,
    ),
    ArchetypeId.SOCCER_POSSESSION_CORE_2P: ArchetypeSpec(
        archetype_id=ArchetypeId.SOCCER_POSSESSION_CORE_2P,
        platform="prizepicks",
        slip_type=SlipType.TWO_PICK_POWER,
        sports=frozenset({"SOCCER"}),
        direction=None,
        markets=SOCCER_MARKETS,
        allow_goblin=True,
        min_legs=2,
        max_legs=2,
    ),
    ArchetypeId.SOCCER_POSSESSION_STACK_FLEX: ArchetypeSpec(
        archetype_id=ArchetypeId.SOCCER_POSSESSION_STACK_FLEX,
        platform="prizepicks",
        slip_type=SlipType.SIX_PICK_FLEX,
        sports=frozenset({"SOCCER"}),
        direction=None,
        markets=SOCCER_MARKETS,
        flex_allowed=True,
        max_legs_per_game=6,
        min_legs=6,
        max_legs=6,
    ),
    ArchetypeId.CS2_KILLS_UNDER_POWER: ArchetypeSpec(
        archetype_id=ArchetypeId.CS2_KILLS_UNDER_POWER,
        platform="prizepicks",
        slip_type=None,  # 2 or 4 pick
        sports=frozenset({"CS2"}),
        direction=None,
        markets=CS2_MARKETS,
        min_legs=2,
        max_legs=4,
    ),
    ArchetypeId.KALSHI_NFL_QB_STACK_SGP: ArchetypeSpec(
        archetype_id=ArchetypeId.KALSHI_NFL_QB_STACK_SGP,
        platform="kalshi",
        sports=frozenset({"NFL"}),
        direction=None,
        markets=frozenset(
            {
                "nfl_pass_yards",
                "nfl_pass_tds",
                "nfl_rec_yards",
                "nfl_receptions",
                "nfl_anytime_td",
            }
        ),
        min_legs=2,
        max_legs=3,
    ),
    ArchetypeId.KALSHI_SAME_PLAYER_DOUBLE_SGP: ArchetypeSpec(
        archetype_id=ArchetypeId.KALSHI_SAME_PLAYER_DOUBLE_SGP,
        platform="kalshi",
        slip_type=SlipType.KALSHI_SGP_2,
        sports=frozenset({"NBA", "NFL"}),
        direction=None,
        markets=frozenset(
            {
                "nfl_pass_yards",
                "nfl_pass_tds",
                "nfl_receptions",
                "nfl_rec_yards",
                "nba_points",
                "nba_assists",
                "nba_rebounds",
            }
        ),
        min_legs=2,
        max_legs=2,
    ),
    ArchetypeId.KALSHI_NBA_BOARDS_DIMES_SGP: ArchetypeSpec(
        archetype_id=ArchetypeId.KALSHI_NBA_BOARDS_DIMES_SGP,
        platform="kalshi",
        sports=frozenset({"NBA"}),
        direction=None,
        markets=frozenset({"nba_rebounds", "nba_assists", "nba_pra"}),
        min_legs=2,
        max_legs=3,
    ),
}


def _micro_volume_line_ok(leg: PropLegCandidate) -> bool:
    if leg.market == "free_throws_made" and leg.line > 1.5:
        return False
    if leg.market == "turnovers" and leg.line > 0.5:
        return False
    return True


def _filter_candidates(
    *,
    spec: ArchetypeSpec,
    candidates: Sequence[PropLegCandidate],
    captured_at: Optional[datetime] = None,
) -> list[PropLegCandidate]:
    if spec.ct_window is not None:
        if captured_at is None:
            _raise_slip_error(
                f"captured_at required for {spec.archetype_id.value}",
                loc=("captured_at",),
            )
        captured = _ensure_aware(captured_at)
        if not spec.ct_window.contains(captured):
            _raise_slip_error(
                f"captured_at outside CT window for {spec.archetype_id.value}",
                loc=("captured_at",),
            )

    eligible: list[PropLegCandidate] = []
    for leg in candidates:
        if leg.platform != spec.platform:
            continue
        if leg.sport not in spec.sports:
            continue
        if not signal_source_allowed(leg, archetype=spec.archetype_id):
            continue
        if not passes_bump_decay_gate(leg):
            continue
        if spec.platform == "prizepicks" and not leg.is_confirmed_active:
            continue
        if spec.markets and leg.market not in spec.markets:
            continue
        if (
            spec.direction is not None
            and leg.direction != spec.direction
            and not leg.is_promo_square
        ):
            continue
        if (
            not spec.allow_goblin
            and leg.modifier == Modifier.GOBLIN
        ):
            continue
        if (
            spec.archetype_id == ArchetypeId.NBA_MICRO_VOLUME_OVER
            and not _micro_volume_line_ok(leg)
        ):
            continue
        if spec.platform == "kalshi":
            if leg.market in KALSHI_HARD_BANNED_MARKETS:
                continue
            # Standalone nba_points banned except cutter-gated same-player double.
            if (
                leg.market == "nba_points"
                and spec.archetype_id != ArchetypeId.KALSHI_SAME_PLAYER_DOUBLE_SGP
            ):
                continue
            if (
                leg.market == "nba_points"
                and MatchupTag.CUTTER_PUTBACK_FLOOR not in leg.matchup_tags
            ):
                continue
        eligible.append(leg)

    eligible.sort(key=rank_key, reverse=True)
    deduped: list[PropLegCandidate] = []
    seen: set[str] = set()
    for leg in eligible:
        if leg.leg_id in seen:
            continue
        seen.add(leg.leg_id)
        deduped.append(leg)
    return deduped


def _count_demons(legs: Sequence[PropLegCandidate | SlipLeg]) -> int:
    return sum(1 for leg in legs if leg.modifier == Modifier.DEMON)


def _count_goblins(legs: Sequence[PropLegCandidate | SlipLeg]) -> int:
    return sum(1 for leg in legs if leg.modifier == Modifier.GOBLIN)


def _select_greedy(
    ranked: list[PropLegCandidate],
    *,
    need: int,
    primary: Optional[Direction],
    max_legs_per_game: Optional[int],
    max_promo: int = 1,
    max_demon: int = 1,
    allow_goblin: bool = False,
    unique_players: bool = True,
) -> list[PropLegCandidate]:
    selected: list[PropLegCandidate] = []
    per_game: dict[str, int] = {}
    demons = 0
    promos = 0
    goblins = 0
    used_players: set[str] = set()
    used_ids: set[str] = set()

    for leg in ranked:
        if len(selected) >= need:
            break
        if leg.leg_id in used_ids:
            continue
        if unique_players and leg.player_or_combo in used_players:
            continue
        if leg.modifier == Modifier.DEMON and demons >= max_demon:
            continue
        if leg.modifier == Modifier.GOBLIN:
            if not allow_goblin or goblins >= 1:
                continue
        is_exception = primary is not None and leg.direction != primary
        if is_exception:
            if not leg.is_promo_square or promos >= max_promo:
                continue
        game_count = per_game.get(leg.event_id, 0)
        if max_legs_per_game is not None and game_count >= max_legs_per_game:
            continue
        selected.append(leg)
        used_ids.add(leg.leg_id)
        used_players.add(leg.player_or_combo)
        per_game[leg.event_id] = game_count + 1
        if leg.modifier == Modifier.DEMON:
            demons += 1
        if leg.modifier == Modifier.GOBLIN:
            goblins += 1
        if is_exception:
            promos += 1
    return selected


def _select_exact_game_stack(
    ranked: list[PropLegCandidate],
    *,
    games_needed: int,
    legs_per: int,
    primary: Direction,
) -> list[PropLegCandidate]:
    by_game: dict[str, list[PropLegCandidate]] = {}
    for leg in ranked:
        by_game.setdefault(leg.event_id, []).append(leg)

    game_options: dict[str, list[tuple[PropLegCandidate, ...]]] = {}
    for gid, pool in by_game.items():
        if len(pool) < legs_per:
            continue
        options: list[tuple[PropLegCandidate, ...]] = []
        for combo in combinations(pool, legs_per):
            if _count_demons(combo) > 1:
                continue
            promos = sum(1 for leg in combo if leg.direction != primary)
            if promos > 1:
                continue
            if len({leg.player_or_combo for leg in combo}) < len(combo):
                continue
            options.append(combo)
        if options:
            options.sort(
                key=lambda c: (
                    sum(rank_score(x) for x in c),
                    sum(x.calibrated_probability for x in c),
                ),
                reverse=True,
            )
            game_options[gid] = options

    if len(game_options) < games_needed:
        _raise_slip_error(
            f"requires exact {games_needed}x{legs_per} game stack"
        )

    best: list[PropLegCandidate] | None = None
    best_score: tuple[float, float] | None = None
    game_ids = sorted(
        game_options.keys(),
        key=lambda gid: (
            sum(rank_score(x) for x in game_options[gid][0]),
            sum(x.calibrated_probability for x in game_options[gid][0]),
        ),
        reverse=True,
    )

    for chosen_games in combinations(game_ids, games_needed):
        def search(
            idx: int,
            acc: list[PropLegCandidate],
            demons: int,
            promos: int,
        ) -> None:
            nonlocal best, best_score
            if idx == len(chosen_games):
                score = (
                    sum(rank_score(x) for x in acc),
                    sum(x.calibrated_probability for x in acc),
                )
                if best is None or (best_score is not None and score > best_score):
                    best = list(acc)
                    best_score = score
                return
            gid = chosen_games[idx]
            for pair in game_options[gid]:
                d = _count_demons(pair)
                p = sum(1 for leg in pair if leg.direction != primary)
                if demons + d > 1 or promos + p > 1:
                    continue
                pair_players = {leg.player_or_combo for leg in pair}
                if pair_players & {leg.player_or_combo for leg in acc}:
                    continue
                search(idx + 1, acc + list(pair), demons + d, promos + p)

        search(0, [], 0, 0)

    if best is None:
        _raise_slip_error(
            f"requires exact {games_needed}x{legs_per} game stack"
        )
    return best


def _validate_prizepicks_global(legs: list[PropLegCandidate], *, allow_goblin: bool) -> None:
    if _count_demons(legs) > 1:
        _raise_slip_error("DEMON count exceeds 1")
    goblins = _count_goblins(legs)
    if goblins and not allow_goblin:
        _raise_slip_error("GOBLIN modifiers are hard-banned for this archetype")
    if goblins > 1:
        _raise_slip_error("at most 1 GOBLIN allowed")


def _assemble_prizepicks(
    *,
    spec: ArchetypeSpec,
    ranked: list[PropLegCandidate],
    slips_built_today: int,
) -> tuple[list[PropLegCandidate], SlipType, float]:
    aid = spec.archetype_id

    if aid == ArchetypeId.NBA_HALF_PACE_FADE:
        assert spec.exact_game_stack is not None and spec.direction is not None
        selected = _select_exact_game_stack(
            ranked,
            games_needed=spec.exact_game_stack[0],
            legs_per=spec.exact_game_stack[1],
            primary=spec.direction,
        )
        _validate_prizepicks_global(selected, allow_goblin=False)
        return selected, SlipType.SIX_PICK_POWER, POWER_MULTIPLIERS[SlipType.SIX_PICK_POWER]

    if aid == ArchetypeId.NFL_1Q_KICKER_COMBO:
        assert spec.direction is not None
        kickers = [
            leg
            for leg in ranked
            if leg.market == "fg_made_combo_1q" and leg.line == 0.5
        ]
        qb = [
            leg
            for leg in ranked
            if leg.market in {"qb_fantasy_score_1q", "pass_attempts_1q"}
        ]
        # Prefer 3–4 kickers + 1–2 QB legs totaling 5.
        best: list[PropLegCandidate] | None = None
        best_key: tuple[float, float] | None = None
        for k_count in (4, 3):
            q_count = 5 - k_count
            if q_count < 1 or q_count > 2:
                continue
            if len(kickers) < k_count or len(qb) < q_count:
                continue
            for k_combo in combinations(kickers[:8], k_count):
                for q_combo in combinations(qb[:6], q_count):
                    cand = list(k_combo) + list(q_combo)
                    if len({c.player_or_combo for c in cand}) < 5:
                        continue
                    if _count_demons(cand) > 1:
                        continue
                    promos = sum(
                        1 for c in cand if c.direction != spec.direction
                    )
                    if promos > 1:
                        continue
                    key = (
                        sum(rank_score(c) for c in cand),
                        sum(c.calibrated_probability for c in cand),
                    )
                    if best is None or (best_key is not None and key > best_key):
                        best = cand
                        best_key = key
        if best is None:
            _raise_slip_error(
                "nfl_1q_kicker_combo requires 3-4 fg_made_combo_1q@0.5 + 1-2 QB legs"
            )
        _validate_prizepicks_global(best, allow_goblin=False)
        return best, SlipType.FIVE_PICK_POWER, POWER_MULTIPLIERS[SlipType.FIVE_PICK_POWER]

    if aid == ArchetypeId.CORE_2P_VALUE_POWER:
        if slips_built_today >= CORE_2P_MAX_SLIPS_PER_DAY:
            _raise_slip_error(
                f"core_2p_value_power max_slips_per_day={CORE_2P_MAX_SLIPS_PER_DAY}",
                loc=("archetype",),
            )
        # Prefer pairs clearing LB bar, else promo+volume.
        best = None
        best_key = None
        for a, b in combinations(ranked[:40], 2):
            if a.event_id != b.event_id and len({a.event_id, b.event_id}) > 2:
                continue
            if a.player_or_combo == b.player_or_combo:
                continue
            if _count_demons([a, b]) > 1:
                continue
            if _count_goblins([a, b]) > 0:
                continue
            games = {a.event_id, b.event_id}
            if len(games) > 2:
                continue
            min_lb = min(a.calibrated_lower_bound, b.calibrated_lower_bound)
            promo_anchor = (
                (a.is_promo_square and b.market in CORE_2P_VOLUME_MARKETS)
                or (b.is_promo_square and a.market in CORE_2P_VOLUME_MARKETS)
            )
            if min_lb < CORE_2P_MIN_LOWER_BOUND and not promo_anchor:
                continue
            key = (min_lb, a.calibrated_probability + b.calibrated_probability)
            if best is None or (best_key is not None and key > best_key):
                best = [a, b]
                best_key = key
        if best is None:
            _raise_slip_error(
                "core_2p_value_power requires min_lower_bound>=0.58 or promo+volume pair"
            )
        _validate_prizepicks_global(best, allow_goblin=False)
        return best, SlipType.TWO_PICK_POWER, POWER_MULTIPLIERS[SlipType.TWO_PICK_POWER]

    if aid == ArchetypeId.SOCCER_POSSESSION_CORE_2P:
        best = None
        best_key = None
        for a, b in combinations(ranked[:40], 2):
            if a.event_id != b.event_id:
                continue
            goblins = _count_goblins([a, b])
            if goblins > 1:
                continue
            if goblins == 1:
                # Must pair Goblin with a correlated STANDARD in same match.
                if not (
                    (a.modifier == Modifier.GOBLIN and b.modifier == Modifier.STANDARD)
                    or (b.modifier == Modifier.GOBLIN and a.modifier == Modifier.STANDARD)
                ):
                    continue
            if _count_demons([a, b]) > 1:
                continue
            key = (
                sum(rank_score(x) for x in (a, b)),
                a.calibrated_probability + b.calibrated_probability,
            )
            if best is None or (best_key is not None and key > best_key):
                best = [a, b]
                best_key = key
        if best is None:
            _raise_slip_error(
                "soccer_possession_core_2p requires single-match 2-pick"
            )
        mult = 2.0 if _count_goblins(best) == 1 else 3.0
        return best, SlipType.TWO_PICK_POWER, mult

    if aid == ArchetypeId.SOCCER_POSSESSION_STACK_FLEX:
        selected = _select_greedy(
            ranked,
            need=6,
            primary=None,
            max_legs_per_game=6,
            max_promo=6,  # mixed directions allowed
            allow_goblin=False,
            unique_players=True,
        )
        if len(selected) != 6:
            _raise_slip_error("soccer_possession_stack_flex requires 6 eligible legs")
        events = {leg.event_id for leg in selected}
        if not (1 <= len(events) <= 3):
            _raise_slip_error("soccer flex requires 1-3 matches")
        per_match: dict[str, int] = {}
        for leg in selected:
            per_match[leg.event_id] = per_match.get(leg.event_id, 0) + 1
        if any(c < 2 or c > 6 for c in per_match.values()):
            # Soft: allow if overall 1-3 matches; enforce 2-6 when multi-match.
            if len(events) > 1 and any(c < 2 for c in per_match.values()):
                _raise_slip_error("soccer flex requires 2-6 legs per match")
        _validate_prizepicks_global(selected, allow_goblin=False)
        return (
            selected,
            SlipType.SIX_PICK_FLEX,
            POWER_MULTIPLIERS[SlipType.SIX_PICK_FLEX],
        )

    if aid == ArchetypeId.CS2_KILLS_UNDER_POWER:
        # Try 4-pick then 2-pick.
        for need, slip_type in (
            (4, SlipType.FOUR_PICK_POWER),
            (2, SlipType.TWO_PICK_POWER),
        ):
            selected = _select_greedy(
                ranked,
                need=need,
                primary=None,
                max_legs_per_game=None,
                max_promo=need,
                allow_goblin=False,
                unique_players=True,
            )
            if len(selected) != need:
                continue
            if len({leg.sport for leg in selected}) != 1:
                continue
            less_count = sum(1 for leg in selected if leg.direction == Direction.LESS)
            if need == 2 and less_count != 2:
                continue
            if need == 4:
                more_count = need - less_count
                if less_count / need < 0.75 or more_count > 1:
                    continue
            _validate_prizepicks_global(selected, allow_goblin=False)
            return selected, slip_type, POWER_MULTIPLIERS[slip_type]
        _raise_slip_error(
            "cs2_kills_under_power requires 2-pick all LESS or 4-pick >=75% LESS"
        )

    # Generic directional power plays.
    assert spec.slip_type is not None and spec.direction is not None
    need = {SlipType.FOUR_PICK_POWER: 4, SlipType.FIVE_PICK_POWER: 5, SlipType.SIX_PICK_POWER: 6}[
        spec.slip_type
    ]
    selected = _select_greedy(
        ranked,
        need=need,
        primary=spec.direction,
        max_legs_per_game=spec.max_legs_per_game,
        allow_goblin=False,
        unique_players=True,
    )
    if len(selected) != need:
        _raise_slip_error(
            f"insufficient eligible legs for {aid.value}: need {need}, got {len(selected)}"
        )
    if spec.min_games is not None:
        games = {leg.event_id for leg in selected}
        if len(games) < spec.min_games:
            _raise_slip_error(
                f"{aid.value} requires min_games={spec.min_games}"
            )
    if aid == ArchetypeId.NBA_4_PACK_FADE and len({leg.event_id for leg in selected}) != 4:
        _raise_slip_error("nba_4_pack_fade requires 4 legs across 4 distinct games")
    _validate_prizepicks_global(selected, allow_goblin=False)
    return selected, spec.slip_type, POWER_MULTIPLIERS[spec.slip_type]


def _assemble_kalshi(
    *,
    spec: ArchetypeSpec,
    ranked: list[PropLegCandidate],
    payout_multiplier: float,
) -> tuple[list[PropLegCandidate], SlipType, float]:
    aid = spec.archetype_id
    # Prefer protected legs first (already in rank_key).
    pool = [leg for leg in ranked if leg.sport in KALSHI_ALLOWED_SPORTS]
    pool = [
        leg
        for leg in pool
        if leg.market not in KALSHI_HARD_BANNED_MARKETS
        and not (leg.market == "nba_points" and aid == ArchetypeId.KALSHI_NBA_BOARDS_DIMES_SGP)
    ]

    if aid == ArchetypeId.KALSHI_SAME_PLAYER_DOUBLE_SGP:
        best = None
        best_key = None
        for a, b in combinations(pool[:50], 2):
            if a.event_id != b.event_id:
                continue
            if a.sport != b.sport:
                continue
            if a.player_or_combo != b.player_or_combo:
                continue
            markets = frozenset({a.market, b.market})
            if markets not in SAME_PLAYER_DOUBLE_PAIRS:
                continue
            if markets == frozenset({"nba_points", "nba_rebounds"}):
                if MatchupTag.CUTTER_PUTBACK_FLOOR not in set(a.matchup_tags) | set(
                    b.matchup_tags
                ):
                    continue
            # Hard ban standalone points without tag already handled.
            if "nba_threes" in markets or "nfl_rush_yards" in markets:
                continue
            key = (
                int(a.is_protected) + int(b.is_protected),
                rank_score(a) + rank_score(b),
            )
            if best is None or (best_key is not None and key > best_key):
                best = [a, b]
                best_key = key
        if best is None:
            _raise_slip_error(
                "kalshi_same_player_double_sgp requires synergistic same-player pair"
            )
        return best, SlipType.KALSHI_SGP_2, payout_multiplier

    if aid == ArchetypeId.KALSHI_NFL_QB_STACK_SGP:
        best = None
        best_key = None
        qb_markets = {"nfl_pass_yards", "nfl_pass_tds"}
        corr_markets = {"nfl_rec_yards", "nfl_receptions", "nfl_anytime_td"}
        for n in (3, 2):
            for combo in combinations(pool[:40], n):
                if len({c.event_id for c in combo}) != 1:
                    continue
                if len({c.sport for c in combo}) != 1 or combo[0].sport != "NFL":
                    continue
                markets = [c.market for c in combo]
                if any(m == "nfl_rush_yards" for m in markets):
                    continue
                qb = sum(1 for m in markets if m in qb_markets)
                corr = sum(1 for m in markets if m in corr_markets)
                if qb < 1 or corr < 1:
                    continue
                if qb + corr != n:
                    continue
                key = (
                    sum(1 for c in combo if c.is_protected),
                    sum(rank_score(c) for c in combo),
                )
                if best is None or (best_key is not None and key > best_key):
                    best = list(combo)
                    best_key = key
        if best is None:
            _raise_slip_error(
                "kalshi_nfl_qb_stack_sgp requires QB anchor + correlated legs"
            )
        slip_type = (
            SlipType.KALSHI_SGP_3 if len(best) == 3 else SlipType.KALSHI_SGP_2
        )
        return best, slip_type, payout_multiplier

    if aid == ArchetypeId.KALSHI_NBA_BOARDS_DIMES_SGP:
        best = None
        best_key = None
        allowed = {"nba_rebounds", "nba_assists", "nba_pra"}
        for n in (3, 2):
            for combo in combinations(
                [p for p in pool if p.market in allowed][:40], n
            ):
                if len({c.event_id for c in combo}) != 1:
                    continue
                if any(c.sport != "NBA" for c in combo):
                    continue
                if any(c.market in {"nba_points", "nba_threes"} for c in combo):
                    continue
                key = (
                    sum(1 for c in combo if c.is_protected),
                    sum(rank_score(c) for c in combo),
                )
                if best is None or (best_key is not None and key > best_key):
                    best = list(combo)
                    best_key = key
        if best is None:
            _raise_slip_error(
                "kalshi_nba_boards_dimes_sgp requires 2-3 rebounds/assists/pra legs"
            )
        slip_type = (
            SlipType.KALSHI_SGP_3 if len(best) == 3 else SlipType.KALSHI_SGP_2
        )
        return best, slip_type, payout_multiplier

    _raise_slip_error(f"unsupported kalshi archetype: {aid.value}")
    raise AssertionError("unreachable")


def build_paper_slip(
    archetype: ArchetypeId | str,
    candidates: Sequence[PropLegCandidate | dict[str, Any]],
    captured_at: Optional[datetime] = None,
    *,
    payout_multiplier: Optional[float] = None,
    slips_built_today: int = 0,
    min_lower_bound: float = 0.0,
) -> SlipSnapshot:
    """
    Build a paper-trade slip for one archetype.

    Always returns can_execute=False. Never places live orders.
    """
    archetype_id = (
        archetype if isinstance(archetype, ArchetypeId) else ArchetypeId(archetype)
    )
    if archetype_id not in ARCHETYPES:
        _raise_slip_error(
            f"unsupported archetype: {archetype}", loc=("archetype",)
        )
    spec = ARCHETYPES[archetype_id]

    parsed: list[PropLegCandidate] = []
    for raw in candidates:
        try:
            cand = _parse_candidate(raw)
        except ValidationError as exc:
            msgs = str(exc).lower()
            # Structural pregame violations must surface.
            if "pregame" in msgs or "captured_at must be strictly before" in msgs:
                raise
            if "calibrated_lower_bound" in msgs:
                continue
            if "probability fields" in msgs or "projected_std" in msgs:
                continue
            raise
        if cand.calibrated_lower_bound < min_lower_bound:
            continue
        parsed.append(cand)

    # Capture-time fallback from candidates when caller omits it.
    if captured_at is None and parsed:
        captured_at = max(leg.captured_at for leg in parsed)

    ranked = _filter_candidates(
        spec=spec, candidates=parsed, captured_at=captured_at
    )

    if spec.platform == "prizepicks":
        selected, slip_type, mult = _assemble_prizepicks(
            spec=spec,
            ranked=ranked,
            slips_built_today=slips_built_today,
        )
    else:
        if payout_multiplier is None or payout_multiplier <= 0:
            _raise_slip_error(
                "kalshi slips require positive payout_multiplier",
                loc=("payout_multiplier",),
            )
        # Reject singles / 4+ via assembly; also reject banned sports early.
        if any(leg.sport not in KALSHI_ALLOWED_SPORTS for leg in ranked):
            ranked = [leg for leg in ranked if leg.sport in KALSHI_ALLOWED_SPORTS]
        selected, slip_type, mult = _assemble_kalshi(
            spec=spec,
            ranked=ranked,
            payout_multiplier=float(payout_multiplier),
        )
        if not (2 <= len(selected) <= 3):
            _raise_slip_error("kalshi SGP requires 2-3 legs")
        if len({leg.event_id for leg in selected}) != 1:
            _raise_slip_error("kalshi SGP requires single-game lock")
        if len({leg.sport for leg in selected}) != 1:
            _raise_slip_error("kalshi SGP requires single-sport lock")

    primary = spec.direction
    slip_legs = [_to_slip_leg(leg, primary=primary) for leg in selected]

    # Unit scale decay when any selected leg has moderate adverse bump.
    unit_scale = 1.0
    for leg in selected:
        bump = adverse_bump_for(leg)
        thresh = hard_skip_threshold(leg.market)
        if 0.0 < bump < thresh:
            unit_scale = MODERATE_BUMP_UNIT_SCALE
            break

    probs = [leg.calibrated_probability for leg in selected]
    lowers = [leg.calibrated_lower_bound for leg in selected]
    joint = float(prod(probs)) if probs else 0.0
    ev_roi = (joint * float(mult)) - 1.0
    sources = sorted({leg.signal_source for leg in selected})

    return SlipSnapshot(
        platform=spec.platform,
        archetype=archetype_id,
        slip_type=slip_type,
        payout_multiplier=float(mult),
        recommended_unit_scale=unit_scale,
        joint_calibrated_prob=joint,
        expected_value_roi=ev_roi,
        min_leg_lower_bound=min(lowers) if lowers else 0.0,
        unprotected_leg_count=sum(1 for leg in selected if not leg.is_protected),
        signal_sources=sources,
        legs=slip_legs,
        captured_at=_ensure_aware(captured_at) if captured_at else None,
        can_execute=False,
    )


__all__ = [
    "ALL_ARCHETYPE_IDS",
    "ARCHETYPES",
    "ArchetypeId",
    "ArchetypeSpec",
    "BLOCKED_SIGNAL_SOURCES",
    "CAN_EXECUTE",
    "CORE_2P_MAX_SLIPS_PER_DAY",
    "CORE_2P_MIN_LOWER_BOUND",
    "CtWindow",
    "Direction",
    "LegCandidate",
    "MatchupTag",
    "Modifier",
    "POWER_MULTIPLIERS",
    "PropLegCandidate",
    "SlipLeg",
    "SlipSnapshot",
    "SlipType",
    "ValidationError",
    "adverse_bump_for",
    "build_paper_slip",
    "hard_skip_threshold",
    "matching_tag_count",
    "rank_key",
    "rank_score",
    "signal_source_allowed",
]
