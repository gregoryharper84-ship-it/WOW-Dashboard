"""
js_style_conversion.py — WOW-PATCH-2026-07-07-JS-STYLE-CONVERSION-LAYER

JS-style conversion layer: runs AFTER data-intake / L5/L10 / projection /
cushion-tax and BEFORE slip_builder / final_approval.

Purpose: separate true JS-style edges from fake-discount traps before
Power/Flex slip construction. Assigns a `js_style_label` sub-tag to every
row. The sub-tag does NOT replace the row's main terminal label — it feeds
the final ladder as additional evidence.

Architecture:
  run(row, enrichment)   — per-row gate; mutates row in-place
  run_slip(rows)         — slip-level gate; mutates rows in-place

Caller responsibilities:
  - Supply `js_features` and/or `js_traps` in enrichment when known
    (analyst-provided intelligence the engine cannot auto-detect).
  - Supply `js_env_support` dict for WNBA PRA MORE cluster gate (Gate A).
  - Supply `pitcher_conflict_proof` for pitcher market conflict gate (Gate B).
  - Never loosen Reliability Freeze, PrizePicks EV gate, half-point
    cushion tax, or no-forced-action rules — those run independently.
"""
from __future__ import annotations

from collections import Counter
from typing import Any


# ── Feature vocabulary ──────────────────────────────────────────────────────

JS_VALID_FEATURES: list[str] = [
    "low_threshold_relative_to_role",
    "one_stat_simplicity",
    "participation_or_workload_floor",
    "discount_goblin_demon_line",
    "clear_less_inflation",
    "strong_cushion_vs_projection_or_median",
    "minimal_shooting_efficiency_dependence",
]

FAKE_JS_TRAPS: list[str] = [
    "high_volatility_pra_more",
    "scoring_efficiency_dependent",
    "low_k_less_4_or_lower_without_restriction_proof",
    "soccer_shots_1_5_without_projection_above_2",
    "same_game_wnba_pra_more_stack",
    "thin_cushion_0_5_to_1_0",
    "pitcher_market_conflict",
]

# ── JS sub-tag labels ────────────────────────────────────────────────────────

JS_VALID                      = "JS_VALID"
JS_WATCH_FAKE_DISCOUNT        = "JS_WATCH_FAKE_DISCOUNT"
JS_CLOSE_NO_UPGRADE           = "JS_CLOSE_NO_UPGRADE"
JS_REJECT_BAD_STRUCTURE       = "JS_REJECT_BAD_STRUCTURE"
JS_REJECT_MARKET_CONFLICT     = "JS_REJECT_MARKET_CONFLICT"
JS_REJECT_LOW_K_LESS_TRAP     = "JS_REJECT_LOW_K_LESS_TRAP"
JS_REJECT_SAME_GAME_PRA_CLUSTER = "JS_REJECT_SAME_GAME_PRA_CLUSTER"
JS_REJECT_THIN_CUSHION        = "JS_REJECT_THIN_CUSHION"

# Main-terminal label overrides for hard rejects
MAIN_LABEL_REJECT_BAD_STRUCTURE = "REJECT_BAD_STRUCTURE"

MIN_JS_VALID_FEATURES = 2  # need at least 2 valid features to be JS eligible

# ── Cushion floors (projected_cushion = projection − line for MORE; line − proj for LESS) ──
#
# Hard-reject floors: cushion BELOW these values is JS_REJECT_THIN_CUSHION.
# Default markets (no explicit entry) use HARD_REJECT_CUSHION_FLOOR = 0.5.
# Cushion in [0.5, 1.0] on any market is advisory JS_CLOSE_NO_UPGRADE — NOT
# a hard reject (the row can still enter a 2-pick Power slip).
#
# Market-specific floors above 0.5 always act as hard rejects:
#   WNBA PRA / P+R+A: floor = 2.5  (combo variance demands strong separation)
#   WNBA Points+Rebounds: floor = 2.5
#   WNBA Rebounds standalone: floor = 1.5
#   MLB Pitcher Ks MORE: floor = 1.0
#   MLB Pitcher Ks LESS: floor = 1.25 (1.5 preferred — floor is minimum)
_HARD_CUSHION_FLOORS: dict[str, float] = {
    "wnba_pra":     2.5,
    "wnba_pr":      2.5,
    "wnba_rebound": 1.5,
    "mlb_k_more":   1.0,
    "mlb_k_less":   1.25,
}
# Default hard floor: below 0.5 is always a hard reject (projection is at or below line)
HARD_REJECT_CUSHION_FLOOR: float = 0.5

# Thin-cushion advisory range: [0.5, 1.0] → JS_CLOSE_NO_UPGRADE (not a hard reject)
THIN_CUSHION_ADVISORY_LOW  = 0.5
THIN_CUSHION_ADVISORY_HIGH = 1.0

# Soccer shots 1.5 MORE: projection must be clearly above 2.0
_SOCCER_SHOTS_MIN_PROJECTION = 2.0
_SOCCER_SHOTS_LINE_TRIGGER   = 1.5


# ── Helpers ─────────────────────────────────────────────────────────────────

def _norm_market_key(row: dict) -> str:
    sport = (row.get("sport") or "").lower()
    pt    = (row.get("prop_type") or "").lower()
    side  = (row.get("direction") or "").upper()

    if sport == "wnba":
        if "pra" in pt or ("point" in pt and "rebound" in pt and "assist" in pt):
            return "wnba_pra"
        if "point" in pt and "rebound" in pt:
            return "wnba_pr"
        if "rebound" in pt:
            return "wnba_rebound"

    if sport == "mlb" and ("strikeout" in pt or " k" in pt or pt == "pitcher ks"):
        return f"mlb_k_{side.lower()}"

    return "default"


def _cushion_floor(row: dict) -> float:
    """Return the hard-reject cushion floor for this market."""
    return _HARD_CUSHION_FLOORS.get(_norm_market_key(row), HARD_REJECT_CUSHION_FLOOR)


def _get_projection(row: dict) -> float | None:
    """Pull best available projection: l10_median preferred, then l10_avg."""
    l5_l10 = (row.get("gates") or {}).get("l5_l10_ledger") or {}
    med = l5_l10.get("l10_median")
    avg = l5_l10.get("l10_avg")
    return med if med is not None else avg


def _compute_cushion(row: dict, projection: float | None) -> float | None:
    line = row.get("line")
    if projection is None or line is None:
        return None
    side = (row.get("direction") or "").upper()
    if side == "MORE":
        return round(projection - float(line), 3)
    if side == "LESS":
        return round(float(line) - projection, 3)
    return None


def _auto_detect_features(row: dict, cushion: float | None) -> list[str]:
    """
    Auto-detect JS-valid features derivable from the normalised row without
    caller intelligence. Conservative: only fires when confident.
    """
    features: list[str] = []
    pt   = (row.get("prop_type") or "").lower()
    side = (row.get("direction") or "").upper()
    line = row.get("line")

    # one_stat_simplicity: single-stat market (not PRA combos)
    combo_signals = ("+", "pra", "p+r", "p+a", "r+a")
    if pt and not any(s in pt for s in combo_signals):
        features.append("one_stat_simplicity")

    # clear_less_inflation: LESS direction is an inflation-capture bet by nature
    if side == "LESS":
        features.append("clear_less_inflation")

    # strong_cushion_vs_projection_or_median: cushion >= 1.5× the market floor
    if cushion is not None:
        floor = _cushion_floor(row)
        if cushion >= floor * 1.5:
            features.append("strong_cushion_vs_projection_or_median")

    return features


def _auto_detect_traps(row: dict, cushion: float | None) -> list[str]:
    """
    Auto-detect FAKE_JS_TRAPS derivable from the row without caller intelligence.
    """
    traps: list[str] = []
    sport = (row.get("sport") or "").lower()
    pt    = (row.get("prop_type") or "").lower()
    side  = (row.get("direction") or "").upper()
    line  = row.get("line")

    # high_volatility_pra_more: WNBA/NBA PRA MORE is by definition high-variance
    pra_like = "pra" in pt or ("point" in pt and "rebound" in pt and "assist" in pt)
    if pra_like and side == "MORE":
        traps.append("high_volatility_pra_more")

    # scoring_efficiency_dependent: points-only prop depends on shooting efficiency
    if pt in ("points", "pts") and side == "MORE":
        traps.append("scoring_efficiency_dependent")

    # thin_cushion_0_5_to_1_0: marginal cushion — real edge but not strong enough
    if cushion is not None and 0.5 <= cushion <= 1.0:
        traps.append("thin_cushion_0_5_to_1_0")

    # soccer_shots_1_5_without_projection_above_2:
    # auto-detect when line == 1.5 for shots market (projection unavailable → flag)
    if sport == "soccer" and "shot" in pt and side == "MORE":
        if line is not None and float(line) <= _SOCCER_SHOTS_LINE_TRIGGER:
            traps.append("soccer_shots_1_5_without_projection_above_2")

    return traps


# ── Gate helpers ─────────────────────────────────────────────────────────────

def _gate_b_pitcher_conflict(row: dict, enrichment: dict) -> bool:
    """
    Gate B — pitcher market conflict.
    Returns True (conflict detected, gate fires) when the same pitcher has
    outs MORE in this row AND K LESS in another row and no contact-outs proof
    is provided.
    This is a per-row gate; full slip-level detection lives in run_slip().
    Here we check the single-row signal from enrichment.
    """
    proof = enrichment.get("pitcher_conflict_proof") or {}
    has_conflict = enrichment.get("same_pitcher_outs_more_and_k_less", False)
    if not has_conflict:
        return False
    contact_proof = (
        proof.get("low_whiff_profile", False)
        and proof.get("low_k_projection", False)
        and proof.get("low_opp_k_rate", False)
        and proof.get("pitch_count_supports_outs", False)
        and proof.get("market_not_implying_k_upside", False)
    )
    return not contact_proof


def _gate_c_low_k_less(row: dict, enrichment: dict) -> bool:
    """
    Gate C — pitcher K LESS at 4.0 or below without all restriction proofs.
    Returns True (gate fires, block Power/Flex) when proof is incomplete.
    """
    pt   = (row.get("prop_type") or "").lower()
    side = (row.get("direction") or "").upper()
    line = row.get("line")
    if side != "LESS":
        return False
    if not ("strikeout" in pt or " k" in pt or pt == "pitcher ks"):
        return False
    if line is None or float(line) > 4.0:
        return False

    proof = enrichment.get("k_less_proof") or {}
    has_all = (
        proof.get("pitch_count_restriction", False)
        and proof.get("low_whiff_profile", False)
        and proof.get("low_opp_k_rate", False)
        and proof.get("early_hook_risk", False)
    )
    return not has_all


def _gate_d_thin_cushion(row: dict, cushion: float | None) -> tuple[bool, float]:
    """
    Gate D — thin cushion hard reject.

    Returns (gate_fires, hard_floor) where gate_fires = True means the
    cushion is below the hard floor for this market type and the row must
    be capped at JS_REJECT_THIN_CUSHION.

    NOTE: The advisory thin-cushion range [0.5, 1.0] produces JS_CLOSE_NO_UPGRADE
    and is handled separately in run() — it is NOT a hard reject. Gate D only
    fires when cushion is truly below the hard floor:
      - Default/standard markets: hard floor = 0.5 (below thin-advisory range)
      - Market-specific high floors (WNBA PRA=2.5, WNBA REB=1.5, MLB K=1.0/1.25):
        cushion < that floor is a hard reject even if cushion ≥ 0.5.
    """
    floor = _cushion_floor(row)

    # Soccer shots special case: projection must be clearly above 2.0
    sport = (row.get("sport") or "").lower()
    pt    = (row.get("prop_type") or "").lower()
    side  = (row.get("direction") or "").upper()
    line  = row.get("line")
    if sport == "soccer" and "shot" in pt and side == "MORE":
        if line is not None and float(line) <= _SOCCER_SHOTS_LINE_TRIGGER:
            proj = _get_projection(row)
            if proj is None or proj <= _SOCCER_SHOTS_MIN_PROJECTION:
                return True, floor

    if cushion is None:
        return False, floor  # unknown cushion → no constraint (honest failure)
    return cushion < floor, floor


# ── Per-row gate ─────────────────────────────────────────────────────────────

def run(row: dict[str, Any], enrichment: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    JS style per-row gate. Mutates the row in-place with:
      gates["js_style"]  — full gate result dict
      js_style_label     — top-level convenience field
      js_close_no_upgrade, projected_cushion, js_valid_features, js_trap_flags,
      js_valid_feature_count, same_game_cluster_id, duplicate_exposure_count,
      pitcher_market_conflict, slip_structure_allowed  (ledger fields)

    Does NOT set terminal_label — JS labels are sub-tags, not terminal labels.
    Hard-reject labels (REJECT_BAD_STRUCTURE etc.) are surfaced as blockers
    and recorded in js_style_label.
    """
    enrichment = enrichment or {}
    blockers: list[str] = row.setdefault("blockers", [])

    # ── Projection + cushion ─────────────────────────────────────────────────
    projection    = _get_projection(row)
    cushion       = _compute_cushion(row, projection)
    cushion_floor = _cushion_floor(row)

    # ── Feature detection ────────────────────────────────────────────────────
    # Caller-supplied intelligence (from analyst enrichment) takes precedence;
    # auto-detected features fill the gaps.
    caller_features: list[str] = list(enrichment.get("js_features") or [])
    caller_traps:    list[str] = list(enrichment.get("js_traps") or [])

    auto_features = _auto_detect_features(row, cushion)
    auto_traps    = _auto_detect_traps(row, cushion)

    # Merge (caller-supplied validated, auto-detected added for any not already listed)
    all_features = list(dict.fromkeys(caller_features + [
        f for f in auto_features if f not in caller_features
    ]))
    all_traps = list(dict.fromkeys(caller_traps + [
        t for t in auto_traps if t not in caller_traps
    ]))

    # Only count features that are in the canonical JS_VALID_FEATURES vocabulary
    valid_features = [f for f in all_features if f in JS_VALID_FEATURES]
    valid_feature_count = len(valid_features)

    # ── Hard gates ────────────────────────────────────────────────────────────

    js_style_label   = None
    js_close_no_upgrade = False
    slip_allowed     = True
    gate_blocker     = None
    gate_source      = None

    # Gate B — pitcher market conflict
    if _gate_b_pitcher_conflict(row, enrichment):
        js_style_label = JS_REJECT_MARKET_CONFLICT
        gate_blocker   = "JS:PITCHER_MARKET_CONFLICT:CONTACT_OUTS_PATH_NOT_PROVEN"
        gate_source    = "gate_b"
        slip_allowed   = False
        all_traps      = list(dict.fromkeys(all_traps + ["pitcher_market_conflict"]))

    # Gate C — low K LESS trap
    elif _gate_c_low_k_less(row, enrichment):
        js_style_label = JS_REJECT_LOW_K_LESS_TRAP
        gate_blocker   = "JS:LOW_K_LESS_TRAP:PROOF_INCOMPLETE"
        gate_source    = "gate_c"
        slip_allowed   = False
        all_traps      = list(dict.fromkeys(all_traps + [
            "low_k_less_4_or_lower_without_restriction_proof"
        ]))

    # Gate D — thin cushion (per market type)
    elif _gate_d_thin_cushion(row, cushion)[0]:
        js_style_label = JS_REJECT_THIN_CUSHION
        gate_blocker   = (
            f"JS:THIN_CUSHION:cushion={cushion} < floor={cushion_floor} "
            f"(market={_norm_market_key(row)})"
        )
        gate_source    = "gate_d"
        slip_allowed   = False
        all_traps      = list(dict.fromkeys(all_traps + ["thin_cushion_0_5_to_1_0"]))

    # ── Feature-count label assignment (if no hard gate fired) ───────────────
    if js_style_label is None:
        only_discount_or_goblin = (
            valid_feature_count >= 1
            and all(
                f in ("discount_goblin_demon_line", "clear_less_inflation")
                for f in valid_features
            )
        )

        if valid_feature_count >= MIN_JS_VALID_FEATURES and not only_discount_or_goblin:
            js_style_label = JS_VALID
        elif only_discount_or_goblin or (
            valid_feature_count >= 1
            and any(t in FAKE_JS_TRAPS for t in all_traps)
        ):
            js_style_label = JS_WATCH_FAKE_DISCOUNT
        elif valid_feature_count >= 1:
            # Has some features but not enough for full JS validation
            js_style_label = JS_WATCH_FAKE_DISCOUNT
        else:
            js_style_label = JS_REJECT_BAD_STRUCTURE
            gate_blocker   = "JS:NO_VALID_FEATURES:REJECT_BAD_STRUCTURE"
            gate_source    = "feature_count"
            slip_allowed   = False

    # ── Thin-cushion close-no-upgrade (advisory, not a hard reject) ──────────
    # Advisory override only applies to rows that would otherwise be JS_VALID.
    # WATCH labels (JS_WATCH_FAKE_DISCOUNT) and REJECT labels are not overridden
    # — they already represent a weaker or harder outcome and should not be
    # silently promoted/changed by the cushion advisory.
    _no_override_labels = {
        JS_REJECT_THIN_CUSHION, JS_REJECT_BAD_STRUCTURE,
        JS_REJECT_MARKET_CONFLICT, JS_REJECT_LOW_K_LESS_TRAP,
        JS_REJECT_SAME_GAME_PRA_CLUSTER,
        JS_WATCH_FAKE_DISCOUNT,  # already a watch — don't override
    }
    if (
        cushion is not None
        and THIN_CUSHION_ADVISORY_LOW <= cushion <= THIN_CUSHION_ADVISORY_HIGH
        and js_style_label not in _no_override_labels
    ):
        js_close_no_upgrade = True
        js_style_label = JS_CLOSE_NO_UPGRADE

    # ── Slip structure allowed ────────────────────────────────────────────────
    # A WATCH or REJECT label blocks Power/Flex; JS_VALID and JS_CLOSE_NO_UPGRADE
    # do not block (close-no-upgrade is advisory — no archetype upgrade, but the
    # leg is not removed from the slip).
    reject_labels = {
        JS_REJECT_BAD_STRUCTURE, JS_REJECT_MARKET_CONFLICT,
        JS_REJECT_LOW_K_LESS_TRAP, JS_REJECT_SAME_GAME_PRA_CLUSTER,
        JS_REJECT_THIN_CUSHION,
    }
    if js_style_label in reject_labels:
        slip_allowed = False

    # Add blocker to row if a hard gate fired
    if gate_blocker and gate_blocker not in blockers:
        blockers.append(gate_blocker)

    # ── Write ledger fields ───────────────────────────────────────────────────
    row["js_style_label"]          = js_style_label
    row["js_valid_features"]       = valid_features
    row["js_valid_feature_count"]  = valid_feature_count
    row["js_trap_flags"]           = all_traps
    row["js_close_no_upgrade"]     = js_close_no_upgrade
    row["projected_cushion"]       = cushion
    row["slip_structure_allowed"]  = slip_allowed
    row["pitcher_market_conflict"] = "pitcher_market_conflict" in all_traps
    # same_game_cluster_id and duplicate_exposure_count filled in run_slip()
    row.setdefault("same_game_cluster_id",     None)
    row.setdefault("duplicate_exposure_count", 0)

    # ── Gate result dict ──────────────────────────────────────────────────────
    row.setdefault("gates", {})["js_style"] = {
        "js_style_label":         js_style_label,
        "js_valid_features":      valid_features,
        "js_valid_feature_count": valid_feature_count,
        "js_trap_flags":          all_traps,
        "js_close_no_upgrade":    js_close_no_upgrade,
        "projected_cushion":      cushion,
        "cushion_floor":          cushion_floor,
        "slip_structure_allowed": slip_allowed,
        "gate_source":            gate_source,
        "passed":                 js_style_label == JS_VALID,
    }

    return row


# ── Slip-level gate ───────────────────────────────────────────────────────────

def run_slip(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Slip-level JS enforcement after per-row run() has been called.

    Gate A — same-game WNBA PRA MORE cluster:
      If 2+ rows share the same game AND are WNBA PRA MORE AND env support
      is missing → reject all of them as JS_REJECT_SAME_GAME_PRA_CLUSTER.

    Duplicate exposure tracking:
      Same player/market/side appearing in multiple rows → increment
      duplicate_exposure_count. Does not reject; the slip builder uses this
      to apply the JS_ANCHOR_OF_SLATE override gate.

    Slip builder structure rules:
      - JS_VALID rows can enter 2-pick Power (always allowed).
      - 3-pick: blocked unless all 3 are JS_VALID and independent (no
        same-game pair of PRA MORE).
      - 4-5 pick Flex: blocked unless every leg is JS_VALID, slip_structure_allowed,
        and no repeated cluster.
    These are recorded in each row's gates["js_style"]["slip_rule"] field.
    """
    # ── duplicate exposure count ─────────────────────────────────────────────
    exposure_key_counts: Counter = Counter()
    for row in rows:
        player = (row.get("player") or "").lower()
        pt     = (row.get("prop_type") or "").lower()
        side   = (row.get("direction") or "").upper()
        exposure_key_counts[f"{player}:{pt}:{side}"] += 1

    for row in rows:
        player = (row.get("player") or "").lower()
        pt     = (row.get("prop_type") or "").lower()
        side   = (row.get("direction") or "").upper()
        key    = f"{player}:{pt}:{side}"
        row["duplicate_exposure_count"] = max(0, exposure_key_counts[key] - 1)
        gs = row.get("gates", {}).get("js_style", {})
        if isinstance(gs, dict):
            gs["duplicate_exposure_count"] = row["duplicate_exposure_count"]

    # ── same-game WNBA PRA MORE cluster (Gate A) ─────────────────────────────
    # Group by game
    game_to_rows: dict[str, list[dict]] = {}
    for row in rows:
        game = (row.get("game") or "").lower().strip()
        if game:
            game_to_rows.setdefault(game, []).append(row)

    # Assign same_game_cluster_id
    cluster_id_map: dict[str, str] = {}
    for game, group in game_to_rows.items():
        cid = f"cluster:{game}"
        for row in group:
            row["same_game_cluster_id"] = cid
            gs = row.get("gates", {}).get("js_style", {})
            if isinstance(gs, dict):
                gs["same_game_cluster_id"] = cid
        cluster_id_map[game] = cid

    for game, group in game_to_rows.items():
        sport = (group[0].get("sport") or "").upper()
        if sport != "WNBA":
            continue

        pra_more_rows = [
            r for r in group
            if (
                _is_wnba_pra_more(r)
                and r.get("js_style_label") not in (
                    JS_REJECT_THIN_CUSHION, JS_REJECT_BAD_STRUCTURE,
                    JS_REJECT_MARKET_CONFLICT, JS_REJECT_LOW_K_LESS_TRAP,
                )
            )
        ]

        if len(pra_more_rows) < 2:
            continue

        # Cluster detected — require full env support on EACH row
        for row in pra_more_rows:
            env = (row.get("gates", {}).get("js_style") or {}).get(
                "js_env_support"
            ) or (row.get("enrichment_meta") or {}).get("js_env_support") or {}

            _ENV_REQUIRED = (
                "pace_support", "total_support", "minutes_support",
                "role_support", "usage_support", "game_environment_support",
            )
            env_ok = all(env.get(k, False) for k in _ENV_REQUIRED)

            if not env_ok:
                row["js_style_label"]   = JS_REJECT_SAME_GAME_PRA_CLUSTER
                row["slip_structure_allowed"] = False
                blocker = (
                    "JS:SAME_GAME_WNBA_PRA_MORE_CLUSTER:ENV_SUPPORT_INCOMPLETE"
                )
                if blocker not in row.setdefault("blockers", []):
                    row["blockers"].append(blocker)
                gs = row.get("gates", {}).get("js_style", {})
                if isinstance(gs, dict):
                    gs["js_style_label"] = JS_REJECT_SAME_GAME_PRA_CLUSTER
                    gs["passed"]         = False
                    gs["slip_structure_allowed"] = False

    # ── Slip builder rule annotations ─────────────────────────────────────────
    total = len(rows)
    all_js_valid = all(r.get("js_style_label") == JS_VALID for r in rows)
    all_slip_ok  = all(r.get("slip_structure_allowed", True) for r in rows)

    # Detect any same-game PRA MORE pair
    pra_more_by_game: Counter = Counter()
    for row in rows:
        if _is_wnba_pra_more(row):
            game = (row.get("game") or "unknown").lower()
            pra_more_by_game[game] += 1
    has_same_game_pra_pair = any(v >= 2 for v in pra_more_by_game.values())

    # Any duplicate exposure across paid cards (> 0 means repeat)
    has_duplicate_exposure = any(
        r.get("duplicate_exposure_count", 0) > 0 for r in rows
    )

    for row in rows:
        slip_rule = _determine_slip_rule(
            row,
            total=total,
            all_js_valid=all_js_valid,
            all_slip_ok=all_slip_ok,
            has_same_game_pra_pair=has_same_game_pra_pair,
            has_duplicate_exposure=has_duplicate_exposure,
        )
        gs = row.get("gates", {}).get("js_style", {})
        if isinstance(gs, dict):
            gs["slip_rule"] = slip_rule

    return rows


def _is_wnba_pra_more(row: dict) -> bool:
    sport = (row.get("sport") or "").upper()
    pt    = (row.get("prop_type") or "").lower()
    side  = (row.get("direction") or "").upper()
    pra_like = "pra" in pt or ("point" in pt and "rebound" in pt and "assist" in pt)
    return sport == "WNBA" and pra_like and side == "MORE"


def _determine_slip_rule(
    row: dict,
    *,
    total: int,
    all_js_valid: bool,
    all_slip_ok: bool,
    has_same_game_pra_pair: bool,
    has_duplicate_exposure: bool,
) -> dict[str, Any]:
    """
    Encode slip builder rules per row:
      1. 2-pick Power: always allowed for JS_VALID.
      2. 3-pick Power/Flex: all 3 must be JS_VALID and independent.
      3. 4–5 pick Flex: all must be JS_VALID, slip_structure_allowed,
         high-cushion, independent, no repeated cluster.
      4. Same player/market/side cannot repeat across paid cards unless
         JS_ANCHOR_OF_SLATE.
    """
    label        = row.get("js_style_label")
    slip_ok      = row.get("slip_structure_allowed", True)
    is_anchor    = row.get("js_anchor_of_slate", False)
    dup_count    = row.get("duplicate_exposure_count", 0)

    two_pick_power_ok  = label == JS_VALID and slip_ok
    three_pick_ok      = all_js_valid and all_slip_ok and not has_same_game_pra_pair
    four_five_flex_ok  = (
        all_js_valid and all_slip_ok
        and not has_same_game_pra_pair
        and not has_duplicate_exposure
    )
    dup_blocked        = dup_count > 0 and not is_anchor

    return {
        "two_pick_power_allowed":       two_pick_power_ok,
        "three_pick_power_flex_allowed": three_pick_ok,
        "four_five_flex_allowed":       four_five_flex_ok,
        "duplicate_exposure_blocked":   dup_blocked,
        "reason": (
            "JS_VALID_2PICK_OK"           if two_pick_power_ok and total <= 2 else
            "REJECT_SLIP_STRUCTURE"       if not slip_ok else
            "DUPLICATE_BLOCKED"           if dup_blocked else
            "JS_VALID_MULTILEG_OK"        if three_pick_ok and total <= 3 else
            "FLEX_CLUSTER_BLOCKED"        if has_same_game_pra_pair else
            "FLEX_DUPLICATE_BLOCKED"      if has_duplicate_exposure else
            "JS_VALID_FLEX_OK"            if four_five_flex_ok else
            "WATCH_ADVISORY"
        ),
    }
