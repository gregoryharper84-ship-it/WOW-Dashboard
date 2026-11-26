"""
WOW paper-trade slip builder (WOW_PROP_LANE).

Hard-locked dry-run only: every SlipSnapshot has can_execute=False.
No live execution, order placement, or capital allocation.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from enum import Enum
from itertools import combinations
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

CT = ZoneInfo("America/Chicago")

CAN_EXECUTE: Literal[False] = False
DEFAULT_MIN_LOWER_BOUND = 0.55
MAX_DEMON_LEGS = 1
MAX_PROMO_SQUARE_EXCEPTIONS = 1


class Direction(str, Enum):
    MORE = "MORE"
    LESS = "LESS"


class Modifier(str, Enum):
    NORMAL = "NORMAL"
    DEMON = "DEMON"
    GOBLIN = "GOBLIN"


class PowerProduct(str, Enum):
    FOUR_PICK = "4_pick_power"
    FIVE_PICK = "5_pick_power"
    SIX_PICK = "6_pick_power"


POWER_MULTIPLIERS: dict[PowerProduct, float] = {
    PowerProduct.FOUR_PICK: 10.0,
    PowerProduct.FIVE_PICK: 20.0,
    PowerProduct.SIX_PICK: 37.5,
}

PICK_COUNT: dict[PowerProduct, int] = {
    PowerProduct.FOUR_PICK: 4,
    PowerProduct.FIVE_PICK: 5,
    PowerProduct.SIX_PICK: 6,
}


class ArchetypeId(str, Enum):
    NBA_NICHE_UNDER_EARLY = "nba_niche_under_early"
    NBA_4_PACK_FADE = "nba_4_pack_fade"
    NBA_HALF_PACE_FADE = "nba_half_pace_fade"
    NBA_MICRO_VOLUME_OVER = "nba_micro_volume_over"
    NFL_1Q_KICKER_COMBO = "nfl_1q_kicker_combo"
    NFL_NICHE_FADE = "nfl_niche_fade"


class CtWindow(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: time
    end: time

    def contains(self, captured_at: datetime) -> bool:
        local = captured_at.astimezone(CT).timetz().replace(tzinfo=None)
        # Inclusive start, exclusive end matching typical capture windows.
        return self.start <= local < self.end


class ArchetypeSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    archetype_id: ArchetypeId
    product: PowerProduct
    direction: Direction
    markets: frozenset[str]
    max_legs_per_game: int | None = None
    ct_window: CtWindow | None = None
    # When set, require exactly N games with exactly K legs each.
    exact_game_stack: tuple[int, int] | None = None


ARCHETYPES: dict[ArchetypeId, ArchetypeSpec] = {
    ArchetypeId.NBA_NICHE_UNDER_EARLY: ArchetypeSpec(
        archetype_id=ArchetypeId.NBA_NICHE_UNDER_EARLY,
        product=PowerProduct.SIX_PICK,
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
        ct_window=CtWindow(start=time(4, 30), end=time(6, 30)),
    ),
    ArchetypeId.NBA_4_PACK_FADE: ArchetypeSpec(
        archetype_id=ArchetypeId.NBA_4_PACK_FADE,
        product=PowerProduct.FOUR_PICK,
        direction=Direction.LESS,
        markets=frozenset({"pra", "points", "fantasy_score"}),
        max_legs_per_game=1,
        ct_window=CtWindow(start=time(17, 30), end=time(18, 30)),
    ),
    ArchetypeId.NBA_HALF_PACE_FADE: ArchetypeSpec(
        archetype_id=ArchetypeId.NBA_HALF_PACE_FADE,
        product=PowerProduct.SIX_PICK,
        direction=Direction.LESS,
        markets=frozenset(
            {"pra_1h", "pra_2h", "fantasy_score_1h", "fantasy_score_2h"}
        ),
        max_legs_per_game=2,
        exact_game_stack=(3, 2),
    ),
    ArchetypeId.NBA_MICRO_VOLUME_OVER: ArchetypeSpec(
        archetype_id=ArchetypeId.NBA_MICRO_VOLUME_OVER,
        product=PowerProduct.SIX_PICK,
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
    ),
    ArchetypeId.NFL_1Q_KICKER_COMBO: ArchetypeSpec(
        archetype_id=ArchetypeId.NFL_1Q_KICKER_COMBO,
        product=PowerProduct.FIVE_PICK,
        direction=Direction.MORE,
        markets=frozenset(
            {"fg_made_combo_1q", "qb_fantasy_score_1q", "pass_attempts_1q"}
        ),
    ),
    ArchetypeId.NFL_NICHE_FADE: ArchetypeSpec(
        archetype_id=ArchetypeId.NFL_NICHE_FADE,
        product=PowerProduct.SIX_PICK,
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
    ),
}


class LegCandidate(BaseModel):
    """Eligible market row offered to the paper-trade builder."""

    model_config = ConfigDict(extra="forbid")

    leg_id: str
    player_id: str
    player_name: str
    game_id: str
    market: str
    direction: Direction
    line: float
    modifier: Modifier = Modifier.NORMAL
    calibrated_probability: float
    calibrated_lower_bound: float
    event_start_time: datetime
    is_confirmed_active: bool
    is_promo_square: bool = False

    @field_validator("calibrated_probability", "calibrated_lower_bound")
    @classmethod
    def _unit_interval(cls, value: float) -> float:
        if not (0.0 <= value <= 1.0):
            raise ValueError("probability fields must be in [0.0, 1.0]")
        return value

    @model_validator(mode="after")
    def _bounds_and_active(self) -> LegCandidate:
        if self.calibrated_lower_bound > self.calibrated_probability:
            raise ValueError(
                "calibrated_lower_bound must be <= calibrated_probability"
            )
        if not self.is_confirmed_active:
            raise ValueError("leg must be is_confirmed_active=True")
        if self.modifier == Modifier.GOBLIN:
            raise ValueError("GOBLIN modifiers are hard-banned")
        return self


class SlipLeg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    leg_id: str
    player_id: str
    player_name: str
    game_id: str
    market: str
    direction: Direction
    line: float
    modifier: Modifier
    calibrated_probability: float
    calibrated_lower_bound: float
    event_start_time: datetime
    is_confirmed_active: Literal[True] = True
    is_promo_square: bool = False
    is_direction_exception: bool = False


class SlipSnapshot(BaseModel):
    """Immutable paper-trade slip snapshot. can_execute is permanently False."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    slip_id: UUID = Field(default_factory=uuid4)
    archetype: ArchetypeId
    product: PowerProduct
    multiplier: float
    primary_direction: Direction
    legs: list[SlipLeg]
    captured_at: datetime
    built_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    stake_units: float = 1.0
    lane: Literal["WOW_PROP_LANE"] = "WOW_PROP_LANE"
    can_execute: Literal[False] = False

    @model_validator(mode="after")
    def _enforce_dry_run(self) -> SlipSnapshot:
        if self.can_execute is not False:
            raise ValueError("can_execute must be False (paper-trade only)")
        return self


def _ensure_aware(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def _assert_pregame(captured_at: datetime, event_start_time: datetime) -> None:
    captured = _ensure_aware(captured_at)
    start = _ensure_aware(event_start_time)
    if not (captured < start):
        raise ValidationError.from_exception_data(
            "LegCandidate",
            [
                {
                    "type": "value_error",
                    "loc": ("captured_at",),
                    "input": captured_at,
                    "ctx": {
                        "error": (
                            "captured_at must be strictly before event_start_time "
                            "(pregame integrity)"
                        )
                    },
                }
            ],
        )


def _rank_key(leg: LegCandidate) -> tuple[float, float]:
    return (leg.calibrated_lower_bound, leg.calibrated_probability)


def _filter_eligible(
    *,
    spec: ArchetypeSpec,
    candidates: list[LegCandidate],
    captured_at: datetime,
    min_lower_bound: float,
) -> list[LegCandidate]:
    captured = _ensure_aware(captured_at)
    if spec.ct_window is not None and not spec.ct_window.contains(captured):
        raise ValidationError.from_exception_data(
            "SlipSnapshot",
            [
                {
                    "type": "value_error",
                    "loc": ("captured_at",),
                    "input": captured_at,
                    "ctx": {
                        "error": (
                            f"captured_at outside CT window for "
                            f"{spec.archetype_id.value}"
                        )
                    },
                }
            ],
        )

    eligible: list[LegCandidate] = []
    for leg in candidates:
        if leg.market not in spec.markets:
            continue
        if leg.modifier == Modifier.GOBLIN:
            continue
        if leg.calibrated_lower_bound < min_lower_bound:
            continue
        if not leg.is_confirmed_active:
            continue
        _assert_pregame(captured, leg.event_start_time)
        # Directional lock: matching direction always ok; opposite only as promo square.
        if leg.direction != spec.direction and not leg.is_promo_square:
            continue
        eligible.append(leg)

    eligible.sort(key=_rank_key, reverse=True)
    # After ranking, keep the strongest row per leg_id so duplicates cannot
    # inflate pick counts or paper-trade payouts.
    deduped: list[LegCandidate] = []
    seen_leg_ids: set[str] = set()
    for leg in eligible:
        if leg.leg_id in seen_leg_ids:
            continue
        seen_leg_ids.add(leg.leg_id)
        deduped.append(leg)
    return deduped


def _to_slip_leg(leg: LegCandidate, *, primary: Direction) -> SlipLeg:
    is_exception = leg.direction != primary
    return SlipLeg(
        leg_id=leg.leg_id,
        player_id=leg.player_id,
        player_name=leg.player_name,
        game_id=leg.game_id,
        market=leg.market,
        direction=leg.direction,
        line=leg.line,
        modifier=leg.modifier,
        calibrated_probability=leg.calibrated_probability,
        calibrated_lower_bound=leg.calibrated_lower_bound,
        event_start_time=_ensure_aware(leg.event_start_time),
        is_confirmed_active=True,
        is_promo_square=leg.is_promo_square,
        is_direction_exception=is_exception,
    )


def _pair_budgets(pair: tuple[LegCandidate, ...], *, primary: Direction) -> tuple[int, int]:
    demons = sum(1 for leg in pair if leg.modifier == Modifier.DEMON)
    promos = sum(1 for leg in pair if leg.direction != primary)
    return demons, promos


def _pair_rank(pair: tuple[LegCandidate, ...]) -> tuple[float, float]:
    # Prefer pairs whose members rank higher (sum of ranking keys).
    lowers = sum(leg.calibrated_lower_bound for leg in pair)
    probs = sum(leg.calibrated_probability for leg in pair)
    return (lowers, probs)


def _select_exact_game_stack(
    *,
    spec: ArchetypeSpec,
    ranked: list[LegCandidate],
) -> list[SlipLeg]:
    """Search feasible N×K stacks without greedily burning DEMON/promo budgets."""
    assert spec.exact_game_stack is not None
    games_needed, legs_per = spec.exact_game_stack
    by_game: dict[str, list[LegCandidate]] = {}
    for leg in ranked:
        by_game.setdefault(leg.game_id, []).append(leg)

    # Per-game candidate pairs that are locally feasible under global max budgets.
    game_options: dict[str, list[tuple[LegCandidate, ...]]] = {}
    for gid, pool in by_game.items():
        if len(pool) < legs_per:
            continue
        options: list[tuple[LegCandidate, ...]] = []
        for combo in combinations(pool, legs_per):
            demons, promos = _pair_budgets(combo, primary=spec.direction)
            if demons > MAX_DEMON_LEGS or promos > MAX_PROMO_SQUARE_EXCEPTIONS:
                continue
            options.append(combo)
        if options:
            options.sort(key=_pair_rank, reverse=True)
            game_options[gid] = options

    if len(game_options) < games_needed:
        raise ValidationError.from_exception_data(
            "SlipSnapshot",
            [
                {
                    "type": "value_error",
                    "loc": ("legs",),
                    "input": [],
                    "ctx": {
                        "error": (
                            f"{spec.archetype_id.value} requires exact "
                            f"{games_needed}x{legs_per} game stack"
                        )
                    },
                }
            ],
        )

    best: list[LegCandidate] | None = None
    best_score: tuple[float, float] | None = None
    game_ids = sorted(
        game_options.keys(),
        key=lambda gid: _pair_rank(game_options[gid][0]),
        reverse=True,
    )

    for chosen_games in combinations(game_ids, games_needed):
        # Nested search over pair choices for these games.
        def search(
            idx: int,
            acc: list[LegCandidate],
            demons: int,
            promos: int,
        ) -> None:
            nonlocal best, best_score
            if idx == len(chosen_games):
                score = _pair_rank(tuple(acc))
                if best is None or (best_score is not None and score > best_score):
                    best = list(acc)
                    best_score = score
                return
            gid = chosen_games[idx]
            for pair in game_options[gid]:
                d, p = _pair_budgets(pair, primary=spec.direction)
                if demons + d > MAX_DEMON_LEGS:
                    continue
                if promos + p > MAX_PROMO_SQUARE_EXCEPTIONS:
                    continue
                # PrizePicks: at most one leg per player across the whole slip.
                pair_players = {leg.player_id for leg in pair}
                if len(pair_players) < len(pair):
                    continue
                if pair_players & {leg.player_id for leg in acc}:
                    continue
                search(idx + 1, acc + list(pair), demons + d, promos + p)

        search(0, [], 0, 0)

    if best is None:
        raise ValidationError.from_exception_data(
            "SlipSnapshot",
            [
                {
                    "type": "value_error",
                    "loc": ("legs",),
                    "input": [],
                    "ctx": {
                        "error": (
                            f"{spec.archetype_id.value} requires exact "
                            f"{games_needed}x{legs_per} game stack"
                        )
                    },
                }
            ],
        )

    return [_to_slip_leg(leg, primary=spec.direction) for leg in best]


def _select_legs(
    *,
    spec: ArchetypeSpec,
    ranked: list[LegCandidate],
) -> list[SlipLeg]:
    need = PICK_COUNT[spec.product]
    selected: list[SlipLeg] = []
    per_game: dict[str, int] = {}
    demon_count = 0
    promo_exceptions = 0
    used_leg_ids: set[str] = set()
    used_player_ids: set[str] = set()

    def try_add(leg: LegCandidate) -> bool:
        nonlocal demon_count, promo_exceptions
        if len(selected) >= need:
            return False
        if leg.leg_id in used_leg_ids:
            return False
        if leg.player_id in used_player_ids:
            return False
        if leg.modifier == Modifier.DEMON and demon_count >= MAX_DEMON_LEGS:
            return False
        is_exception = leg.direction != spec.direction
        if is_exception:
            if not leg.is_promo_square:
                return False
            if promo_exceptions >= MAX_PROMO_SQUARE_EXCEPTIONS:
                return False
        game_count = per_game.get(leg.game_id, 0)
        if spec.max_legs_per_game is not None and game_count >= spec.max_legs_per_game:
            return False

        selected.append(_to_slip_leg(leg, primary=spec.direction))
        used_leg_ids.add(leg.leg_id)
        used_player_ids.add(leg.player_id)
        per_game[leg.game_id] = game_count + 1
        if leg.modifier == Modifier.DEMON:
            demon_count += 1
        if is_exception:
            promo_exceptions += 1
        return True

    if spec.exact_game_stack is not None:
        return _select_exact_game_stack(spec=spec, ranked=ranked)

    for leg in ranked:
        try_add(leg)
        if len(selected) >= need:
            break

    if len(selected) != need:
        raise ValidationError.from_exception_data(
            "SlipSnapshot",
            [
                {
                    "type": "value_error",
                    "loc": ("legs",),
                    "input": selected,
                    "ctx": {
                        "error": (
                            f"insufficient eligible legs for "
                            f"{spec.archetype_id.value}: need {need}, got {len(selected)}"
                        )
                    },
                }
            ],
        )

    # nba_4_pack_fade: strict 4 distinct games
    if (
        spec.archetype_id == ArchetypeId.NBA_4_PACK_FADE
        and len({leg.game_id for leg in selected}) != need
    ):
        raise ValidationError.from_exception_data(
            "SlipSnapshot",
            [
                {
                    "type": "value_error",
                    "loc": ("legs",),
                    "input": selected,
                    "ctx": {
                        "error": "nba_4_pack_fade requires 4 legs across 4 distinct games"
                    },
                }
            ],
        )

    return selected


def build_paper_slip(
    archetype: ArchetypeId | str,
    candidates: list[LegCandidate] | list[dict],
    captured_at: datetime,
    *,
    min_lower_bound: float = DEFAULT_MIN_LOWER_BOUND,
) -> SlipSnapshot:
    """
    Build a PrizePicks Power Play paper slip for one archetype.

    Flex Plays are rejected by construction (only Power products are defined).
    """
    archetype_id = (
        archetype if isinstance(archetype, ArchetypeId) else ArchetypeId(archetype)
    )
    if archetype_id not in ARCHETYPES:
        raise ValidationError.from_exception_data(
            "SlipSnapshot",
            [
                {
                    "type": "value_error",
                    "loc": ("archetype",),
                    "input": archetype,
                    "ctx": {"error": f"unsupported archetype: {archetype}"},
                }
            ],
        )
    spec = ARCHETYPES[archetype_id]
    parsed: list[LegCandidate] = []
    for raw in candidates:
        if isinstance(raw, LegCandidate):
            # Re-validate goblin ban even if constructed outside helpers.
            if raw.modifier == Modifier.GOBLIN:
                continue
            parsed.append(raw)
        else:
            try:
                parsed.append(LegCandidate.model_validate(raw))
            except ValidationError as exc:
                # Goblin / inactive / bound failures are filterable at intake when
                # they come from dict payloads; pregame is checked later with
                # captured_at context. Re-raise non-filterable structural errors
                # only when every field is present but pregame/window fails later.
                msgs = str(exc).lower()
                if "goblin" in msgs or "is_confirmed_active" in msgs:
                    continue
                if "calibrated_lower_bound" in msgs and "<=" in msgs:
                    continue
                if "probability fields" in msgs:
                    continue
                raise

    ranked = _filter_eligible(
        spec=spec,
        candidates=parsed,
        captured_at=captured_at,
        min_lower_bound=min_lower_bound,
    )
    legs = _select_legs(spec=spec, ranked=ranked)
    product = spec.product
    return SlipSnapshot(
        archetype=archetype_id,
        product=product,
        multiplier=POWER_MULTIPLIERS[product],
        primary_direction=spec.direction,
        legs=legs,
        captured_at=_ensure_aware(captured_at),
        can_execute=False,
    )


__all__ = [
    "ARCHETYPES",
    "ArchetypeId",
    "ArchetypeSpec",
    "CAN_EXECUTE",
    "CtWindow",
    "DEFAULT_MIN_LOWER_BOUND",
    "Direction",
    "LegCandidate",
    "Modifier",
    "PICK_COUNT",
    "POWER_MULTIPLIERS",
    "PowerProduct",
    "SlipLeg",
    "SlipSnapshot",
    "ValidationError",
    "build_paper_slip",
]
