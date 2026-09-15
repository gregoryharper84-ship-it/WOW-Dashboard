"""V17 leakage-safe team-state features for LLP team/event challenger models.

All outputs are fitted-model inputs/audits. Nothing here manually moves a
published probability, certifies a model, or authorizes execution.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math
from statistics import mean, pstdev
from typing import Any, Iterable, Mapping, Sequence

CAN_EXECUTE = False
FEATURE_FAMILY_VERSION = "TEAM_STATE_INTELLIGENCE_V1"
TREND_DRIVER_STATUSES = {
    "CONFIRMED_STRUCTURAL_DRIVER", "SUPPORTED_TACTICAL_DRIVER",
    "SUPPORTED_PERSONNEL_DRIVER", "SUPPORTED_SCHEDULE_DRIVER",
    "PLAUSIBLE_UNCONFIRMED_DRIVER", "NO_IDENTIFIED_DRIVER",
    "CONTRADICTED_BY_UNDERLYING_METRICS",
}
SOURCE_STATUSES = {
    "SOURCE_OK", "SOURCE_STALE", "SOURCE_AUTH_FAILED", "SOURCE_RATE_LIMITED",
    "SOURCE_SCHEMA_CHANGED", "SOURCE_TIMEOUT", "SOURCE_PAYLOAD_OVERSIZE", "SOURCE_CONFLICT",
}


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(v)))


def _f(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def _dt(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    x = datetime.fromisoformat(str(v or "").strip().replace("Z", "+00:00"))
    return x if x.tzinfo else x.replace(tzinfo=timezone.utc)


def _avg(rows: Sequence[Mapping[str, Any]], key: str, default: float = 0.0) -> float:
    vals = [_f(r.get(key), float("nan")) for r in rows]
    vals = [v for v in vals if math.isfinite(v)]
    return mean(vals) if vals else default


def _rate(rows: Sequence[Mapping[str, Any]]) -> float:
    return mean([1.0 if bool(r.get("won")) else 0.0 for r in rows]) if rows else 0.0


def _streak(rows: Sequence[Mapping[str, Any]]) -> float:
    if not rows:
        return 0.0
    last = bool(rows[-1].get("won")); n = 0
    for row in reversed(rows):
        if bool(row.get("won")) != last:
            break
        n += 1
    return float(n if last else -n)


def _slope(values: Sequence[float]) -> float:
    if len(values) < 3:
        return 0.0
    xb, yb = (len(values) - 1) / 2.0, mean(values)
    denom = sum((i - xb) ** 2 for i in range(len(values)))
    return 0.0 if denom <= 0 else sum((i - xb) * (v - yb) for i, v in enumerate(values)) / denom


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = {str(x) for x in a if str(x)}, {str(x) for x in b if str(x)}
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def season_regime(game_index: int, expected_season_games: int | None) -> str:
    if not expected_season_games or expected_season_games <= 0:
        return "EARLY" if game_index <= 5 else ("MID" if game_index <= 20 else "LATE")
    ratio = game_index / max(int(expected_season_games), 1)
    return "EARLY" if ratio <= .20 else ("MID" if ratio < .75 else "LATE")


@dataclass(frozen=True)
class TeamStateSnapshot:
    games_prior: int
    season_games_prior: int
    streak_signed: float
    win_rate_l3: float
    win_rate_l5: float
    win_rate_l10: float
    point_diff_l3: float
    point_diff_l5: float
    point_diff_l10: float
    season_point_diff: float
    recent_vs_season_point_diff: float
    process_form_l5: float
    result_process_gap_l5: float
    opponent_strength_l5: float
    schedule_adjusted_point_diff_l5: float
    schedule_adjusted_trend: float
    rest_days: float
    travel_load: float
    schedule_congestion: float
    change_point_score: float
    change_point_status: str
    trend_driver_status: str
    trend_driver_count: int
    variance_residual_l5: float
    sustainability_score: float
    unsustainable_variance_score: float
    roster_continuity: float
    lineup_continuity: float
    games_since_structural_change: float
    attack_style_index: float
    defense_style_index: float
    pace_or_tempo_index: float
    matchup_interaction_index: float
    fragility_score: float
    upside_score: float
    season_regime_early: float
    season_regime_mid: float
    season_regime_late: float
    streak_without_driver: float
    results_process_divergence: float
    form_schedule_inflated: float
    form_schedule_suppressed: float

    def as_features(self, prefix: str = "") -> dict[str, float]:
        out = {f"{prefix}{k}": float(v) for k, v in asdict(self).items()
               if isinstance(v, (int, float)) and not isinstance(v, bool)}
        for status in sorted(TREND_DRIVER_STATUSES):
            out[f"{prefix}trend_driver_{status.lower()}"] = float(self.trend_driver_status == status)
        for status in ("STABLE", "POSSIBLE_SHIFT", "CONFIRMED_SHIFT"):
            out[f"{prefix}change_point_{status.lower()}"] = float(self.change_point_status == status)
        return out


def build_team_state(history: Sequence[Mapping[str, Any]], *, target_time: Any,
                     expected_season_games: int | None = None,
                     current_roster: Iterable[str] | None = None,
                     current_lineup: Iterable[str] | None = None,
                     opponent_state: Mapping[str, Any] | None = None,
                     target_season: int | str | None = None) -> TeamStateSnapshot:
    target = _dt(target_time)
    prior = sorted((dict(r) for r in history if r.get("event_time") and _dt(r["event_time"]) < target),
                   key=lambda r: _dt(r["event_time"]))
    n = len(prior)
    season = [r for r in prior if target_season is not None and str(r.get("season")) == str(target_season)] if target_season is not None else prior
    if target_season is not None and not season:
        season = prior[-min(len(prior), 10):]
    season_n = len(season)
    l3, l5, l10 = prior[-3:], prior[-5:], prior[-10:]
    pd3, pd5, pd10 = _avg(l3, "point_diff"), _avg(l5, "point_diff"), _avg(l10, "point_diff")
    season_pd = _avg(season, "point_diff")
    process5 = _avg(l5, "process_margin", pd5)
    result_process_gap = pd5 - process5
    opp5 = _avg(l5, "opponent_strength_prior")
    # Higher opponent strength means a harder slate, so good performance vs a
    # stronger schedule is adjusted upward; weak opposition is discounted.
    schedule_adjusted = pd5 + opp5
    schedule_trend = _slope([_f(r.get("point_diff")) + _f(r.get("opponent_strength_prior")) for r in l10])
    last = _dt(prior[-1]["event_time"]) if prior else target
    rest = _clip((target - last).total_seconds() / 86400.0, 0, 45) if prior else 14.0
    travel, congestion = _avg(l5, "travel_load"), _avg(l5, "congestion")

    previous = prior[-15:-5] if len(prior) >= 10 else prior[:-5]
    prev_process = _avg(previous, "process_margin", _avg(previous, "point_diff"))
    vals = [_f(r.get("process_margin"), _f(r.get("point_diff"))) for r in l5 + previous]
    scale = pstdev(vals) if len(vals) >= 2 else 0.0
    shift = process5 - prev_process
    cp = _clip(1.0 - math.exp(-abs(shift) / max(scale, 1.0)), 0, 1)
    cp_status = "CONFIRMED_SHIFT" if cp >= .70 else ("POSSIBLE_SHIFT" if cp >= .40 else "STABLE")

    tags: list[str] = []
    for row in l5:
        raw = row.get("structural_tags") or []
        raw = [raw] if isinstance(raw, str) else raw
        tags.extend(str(x).upper() for x in raw if str(x).strip())
    tagset = set(tags)
    if any(any(token in t for token in ("ROSTER", "PLAYER", "QB", "GOALIE", "STARTER", "LINEUP")) for t in tagset):
        driver = "SUPPORTED_PERSONNEL_DRIVER"
    elif any(any(token in t for token in ("TACTIC", "SCHEME", "COACH", "FORMATION")) for t in tagset):
        driver = "SUPPORTED_TACTICAL_DRIVER"
    elif any("SCHEDULE" in t or "OPPONENT" in t for t in tagset):
        driver = "SUPPORTED_SCHEDULE_DRIVER"
    elif cp >= .70 and abs(schedule_adjusted) > 1:
        driver = "PLAUSIBLE_UNCONFIRMED_DRIVER"
    elif abs(pd5 - season_pd) >= 2 and abs(schedule_adjusted) < max(.5, abs(pd5 - season_pd) * .25):
        driver = "CONTRADICTED_BY_UNDERLYING_METRICS"
    else:
        driver = "NO_IDENTIFIED_DRIVER"

    sustainability = _clip(1.0 - abs(result_process_gap) / max(2.0, abs(process5) + 2.0), 0, 1)
    unsustainable = 1.0 - sustainability
    prior_roster = prior[-1].get("roster_ids") if prior else None
    prior_lineup = prior[-1].get("lineup_ids") if prior else None
    prev_roster = prior[-2].get("roster_ids") if len(prior) >= 2 else prior_roster
    prev_lineup = prior[-2].get("lineup_ids") if len(prior) >= 2 else prior_lineup
    roster_cont = _jaccard(current_roster, prior_roster or []) if current_roster is not None else _jaccard(prior_roster or [], prev_roster or [])
    lineup_cont = _jaccard(current_lineup, prior_lineup or []) if current_lineup is not None else _jaccard(prior_lineup or [], prev_lineup or [])
    change_idx = max((i for i, r in enumerate(prior) if r.get("structural_tags")), default=None)
    games_since_change = float(n - 1 - change_idx) if change_idx is not None else float(n)

    attack, defense, pace = _avg(l5, "attack_style_index"), _avg(l5, "defense_style_index"), _avg(l5, "pace_or_tempo_index")
    opp = opponent_state or {}
    interaction = (attack - _f(opp.get("defense_style_index"))) - (_f(opp.get("attack_style_index")) - defense) + .25 * (pace - _f(opp.get("pace_or_tempo_index")))
    fragility = _clip(.30 * unsustainable + .20 * _clip(-schedule_trend / 3, 0, 1) + .20 * (1-roster_cont) + .15 * (1-lineup_cont) + .15 * _clip(congestion / 3, 0, 1), 0, 1)
    upside = _clip(.30 * _clip(schedule_trend / 3, 0, 1) + .25 * sustainability + .20 * _clip(max(interaction, 0) / 3, 0, 1) + .15 * cp + .10 * _clip(max(schedule_adjusted, 0) / 5, 0, 1), 0, 1)
    regime = season_regime(season_n + 1, expected_season_games)
    streak = _streak(prior)
    inflated = float(pd5 > 0 and schedule_adjusted < pd5 - 1)
    suppressed = float(pd5 < 0 and schedule_adjusted > pd5 + 1)

    return TeamStateSnapshot(
        n, season_n, streak, _rate(l3), _rate(l5), _rate(l10), pd3, pd5, pd10,
        season_pd, pd5-season_pd, process5, result_process_gap, opp5,
        schedule_adjusted, schedule_trend, rest, travel, congestion, cp, cp_status,
        driver, len(tagset), result_process_gap, sustainability, unsustainable,
        roster_cont, lineup_cont, games_since_change, attack, defense, pace, interaction,
        fragility, upside, float(regime=="EARLY"), float(regime=="MID"), float(regime=="LATE"),
        float(abs(streak) >= 3 and driver in {"NO_IDENTIFIED_DRIVER", "CONTRADICTED_BY_UNDERLYING_METRICS"}),
        float(abs(result_process_gap) >= max(2.0, abs(process5) * .50)), inflated, suppressed,
    )


def paired_matchup_features(home: TeamStateSnapshot, away: TeamStateSnapshot) -> dict[str, float]:
    hf, af = home.as_features("home_"), away.as_features("away_")
    out = {**hf, **af}
    for field in ("win_rate_l5", "point_diff_l5", "process_form_l5", "schedule_adjusted_point_diff_l5",
                  "schedule_adjusted_trend", "sustainability_score", "change_point_score",
                  "roster_continuity", "lineup_continuity", "fragility_score", "upside_score"):
        out[f"{field}_edge"] = hf[f"home_{field}"] - af[f"away_{field}"]
    h = home.attack_style_index - away.defense_style_index
    a = away.attack_style_index - home.defense_style_index
    out.update(home_attack_vs_away_defense=h, away_attack_vs_home_defense=a,
               matchup_style_edge=h-a, pace_interaction=home.pace_or_tempo_index*away.pace_or_tempo_index,
               pace_mismatch=abs(home.pace_or_tempo_index-away.pace_or_tempo_index))
    return out


def champion_challenger_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    usable = []
    for row in rows:
        try:
            y = 1.0 if bool(row["outcome"]) else 0.0
            c = _clip(float(row["champion_probability"]), 1e-9, 1-1e-9)
            h = _clip(float(row["challenger_probability"]), 1e-9, 1-1e-9)
            usable.append((y, c, h))
        except (KeyError, TypeError, ValueError):
            pass
    if not usable:
        return {"n": 0, "status": "NO_COMPARABLE_ROWS", "automatic_promotion": False, "can_execute": False}
    def metrics(idx: int) -> tuple[float, float]:
        return (mean((r[idx]-r[0])**2 for r in usable),
                -mean(r[0]*math.log(r[idx]) + (1-r[0])*math.log(1-r[idx]) for r in usable))
    cb, cl = metrics(1); hb, hl = metrics(2)
    return {"n": len(usable), "champion_brier": cb, "challenger_brier": hb,
            "champion_log_loss": cl, "challenger_log_loss": hl,
            "brier_delta": hb-cb, "log_loss_delta": hl-cl,
            "challenger_better_brier": hb < cb, "challenger_better_log_loss": hl < cl,
            "promotion_eligible_on_metrics": len(usable) >= 100 and hb < cb and hl < cl,
            "automatic_promotion": False, "can_execute": False}


def coverage_report(rows: Sequence[Mapping[str, Any]], *, expected_events: int | None = None) -> dict[str, Any]:
    counts = {"scored": 0, "blocked": 0, "purged": 0, "unprocessed": 0}
    failures: dict[str, int] = {}
    for row in rows:
        status = str(row.get("terminal_status") or row.get("status") or "").upper()
        if status in {"PASS", "SCORED", "MODEL_QUALIFIED", "COMPLETE"}:
            counts["scored"] += 1
        elif "PURG" in status or status in {"EVENT_ALREADY_STARTED", "EVENT_FINISHED"}:
            counts["purged"] += 1
        elif status:
            counts["blocked"] += 1
        else:
            counts["unprocessed"] += 1
        for failure in row.get("source_failures") or []:
            failures[str(failure)] = failures.get(str(failure), 0) + 1
    discovered, expected = len(rows), int(expected_events if expected_events is not None else len(rows))
    terminalized = counts["scored"] + counts["blocked"] + counts["purged"]
    complete = discovered >= expected and terminalized == discovered and counts["unprocessed"] == 0
    return {"expected_events": expected, "discovered_events": discovered, "terminalized_events": terminalized,
            **counts, "coverage_ratio": min(1.0, terminalized/max(expected, 1)),
            "coverage_status": "COMPLETE" if complete else ("PARTIAL" if terminalized else "FAILED"),
            "slate_wide_none_qualified_allowed": complete, "source_failures": failures, "can_execute": False}


def resolve_evidence_field(observations: Sequence[Mapping[str, Any]], *, now: Any,
                           max_age_seconds: float, conflict_tolerance: float = 0.0) -> dict[str, Any]:
    now_dt = _dt(now); candidates = []; failures = []
    for raw in observations:
        row = dict(raw); status = str(row.get("status") or "SOURCE_OK").upper()
        if status != "SOURCE_OK":
            failures.append(status if status in SOURCE_STATUSES else "SOURCE_SCHEMA_CHANGED"); continue
        try:
            ts = _dt(row.get("timestamp"))
        except Exception:
            failures.append("SOURCE_SCHEMA_CHANGED"); continue
        age = max(0.0, (now_dt-ts).total_seconds())
        if age > max_age_seconds:
            failures.append("SOURCE_STALE"); continue
        row.update(_ts=ts, _age=age, _priority=int(row.get("priority") or 100)); candidates.append(row)
    if not candidates:
        return {"status":"DATA_UNOBTAINABLE","resolved":False,"value":None,"source":None,
                "source_failures":sorted(set(failures)),"can_execute":False}
    candidates.sort(key=lambda r:(r["_priority"], -r["_ts"].timestamp()))
    priority = candidates[0]["_priority"]; peers = [r for r in candidates if r["_priority"] == priority]; first = peers[0]
    conflicts = []
    for peer in peers[1:]:
        a,b = first.get("value"), peer.get("value")
        try: conflict = abs(float(a)-float(b)) > conflict_tolerance
        except (TypeError, ValueError): conflict = a != b
        if conflict: conflicts.append(str(peer.get("source") or "UNKNOWN"))
    if conflicts:
        return {"status":"SOURCE_CONFLICT","resolved":False,"value":None,"source":None,
                "conflicting_sources":[str(first.get("source") or "UNKNOWN"),*conflicts],
                "source_failures":sorted(set(failures+["SOURCE_CONFLICT"])),"can_execute":False}
    return {"status":"SOURCE_OK","resolved":True,"value":first.get("value"),"source":first.get("source"),
            "timestamp":first["_ts"].isoformat(),"age_seconds":first["_age"],"fallback_used":priority>0,
            "source_failures":sorted(set(failures)),"can_execute":False}


__all__ = ["CAN_EXECUTE", "FEATURE_FAMILY_VERSION", "TREND_DRIVER_STATUSES", "SOURCE_STATUSES",
           "TeamStateSnapshot", "build_team_state", "paired_matchup_features",
           "champion_challenger_metrics", "coverage_report", "resolve_evidence_field", "season_regime"]
