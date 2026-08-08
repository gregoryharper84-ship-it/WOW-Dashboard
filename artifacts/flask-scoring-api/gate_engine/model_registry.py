"""
gate_engine/model_registry.py

Model registry for hit probability computation.

Enforces the principle: every supported (sport, prop_type) combination maps to
a versioned, deterministic model. Unsupported combinations return NO_REGISTERED_MODEL
and the pipeline fails closed — no Claude estimate, no generic Poisson substitution.

Public API
----------
lookup(sport, stat_key, line=None) -> dict
    Returns a registry entry dict. If unsupported, returns a NO_REGISTERED_MODEL entry.
    Never raises.

is_supported(sport, stat_key, line=None) -> bool

probability_bounds(p, sample_size, model_status) -> (lower, upper)

Entry schema
------------
{
  "model_id":             str,
  "model_version":        str | None,
  "calibration_version":  str | None,
  "status":               "ACTIVE" | "PROVISIONAL" | "NO_REGISTERED_MODEL",
  "minimum_inputs":       list[str],
  "notes":                str,
}

Status meanings
---------------
ACTIVE       — deterministic, versioned formula; results are publishable.
PROVISIONAL  — codified formula exists but uses simplified assumptions
               (e.g. Poisson with λ=game-log mean ignores minutes/role).
               Results are returned but flagged; not suitable for high-confidence
               grading without manual review.
NO_REGISTERED_MODEL — combination not yet codified; returns null probability.
               Surface RESEARCH_INTEREST or NO_SOURCE_COVERAGE — never substitute
               a generic AI estimate.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Helpers (defined BEFORE the registry dict that uses them)
# ---------------------------------------------------------------------------

def _prov(model_id: str, minimum_inputs: list, notes: str = "") -> dict:
    """Shorthand for a PROVISIONAL registry entry."""
    return {
        "model_id":            model_id,
        "model_version":       "1.0",
        "calibration_version": None,
        "status":              "PROVISIONAL",
        "minimum_inputs":      list(minimum_inputs),
        "notes":               notes or "Poisson λ=game-log mean; ignores minutes/role/opponent context",
        # Ceiling enforced in every response that surfaces this registry entry.
        # A high numeric result from a PROVISIONAL model must not override these.
        "provisional_ceiling": {
            "maximum_label":       "MODEL_QUALIFIED_HOLD",
            "power_eligibility":   False,
            "money_grade_allowed": False,
        },
    }


# ---------------------------------------------------------------------------
# Sentinel entry for unsupported combinations
# ---------------------------------------------------------------------------

_NO_REGISTERED_MODEL: dict = {
    "model_id":            "NO_REGISTERED_MODEL",
    "model_version":       None,
    "calibration_version": None,
    "status":              "NO_REGISTERED_MODEL",
    "minimum_inputs":      [],
    "notes":               (
        "No deterministic model registered for this sport/prop combination. "
        "Surface RESEARCH_INTEREST or NO_SOURCE_COVERAGE — never substitute "
        "a generic AI estimate."
    ),
}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: dict = {

    # ── MLB: batter hits 0.5 line ─────────────────────────────────────────
    # Full binomial PA model from mlb/hit_probability_model.py
    ("MLB", "H"): {
        "model_id":            "mlb_hits_binomial_pa_v2",
        "model_version":       "2.0",
        "calibration_version": None,
        "status":              "ACTIVE",
        "minimum_inputs":      ["game_log"],
        "notes":               "P(≥1 hit) = 1−(1−BA)^PA; prefers enrichment BA/PA; falls back to game-log derivation",
    },
    ("MLB", "HITS"): {
        "model_id":            "mlb_hits_binomial_pa_v2",
        "model_version":       "2.0",
        "calibration_version": None,
        "status":              "ACTIVE",
        "minimum_inputs":      ["game_log"],
        "notes":               "Alias for H",
    },

    # ── MLB: near-binary counting props (line ≤ 1.5) ─────────────────────
    # Bernoulli hit rate from game log
    ("MLB", "HR"):  _prov("mlb_binary_bernoulli_v1", ["game_log"]),
    ("MLB", "RBI"): _prov("mlb_binary_bernoulli_v1", ["game_log"]),
    ("MLB", "SB"):  _prov("mlb_binary_bernoulli_v1", ["game_log"]),
    ("MLB", "BB"):  _prov("mlb_binary_bernoulli_v1", ["game_log"]),
    ("MLB", "R"):   _prov("mlb_binary_bernoulli_v1", ["game_log"]),
    ("MLB", "TB"):  _prov("mlb_binary_bernoulli_v1", ["game_log"]),
    ("MLB", "1B"):  _prov("mlb_binary_bernoulli_v1", ["game_log"]),
    ("MLB", "2B"):  _prov("mlb_binary_bernoulli_v1", ["game_log"]),
    ("MLB", "3B"):  _prov("mlb_binary_bernoulli_v1", ["game_log"]),

    # ── MLB: strikeout / innings pitched (Poisson) ────────────────────────
    ("MLB", "SO"):          _prov("mlb_counting_poisson_v1", ["game_log"]),
    ("MLB", "K"):           _prov("mlb_counting_poisson_v1", ["game_log"]),
    ("MLB", "STRIKEOUTS"):  _prov("mlb_counting_poisson_v1", ["game_log"]),
    ("MLB", "TOTAL_BASES"): _prov("mlb_counting_poisson_v1", ["game_log"]),
    ("MLB", "IP"):          _prov("mlb_counting_poisson_v1", ["game_log"]),
    ("MLB", "INNINGS"):     _prov("mlb_counting_poisson_v1", ["game_log"]),
    ("MLB", "OUTS"):        _prov("mlb_counting_poisson_v1", ["game_log"]),

    # ── MLB: plate appearances (Poisson counting) ────────────────────
    # PA is a per-game counting stat well-modelled by Poisson λ=game-log mean.
    # PROVISIONAL until back-tested against settled results.
    ("MLB", "PA"):                _prov("mlb_counting_poisson_v1", ["game_log"],
                                        "Poisson λ=game-log mean plate appearances per game; PROVISIONAL"),
    ("MLB", "PLATE_APPEARANCES"): _prov("mlb_counting_poisson_v1", ["game_log"],
                                        "Alias for PA; Poisson λ=game-log mean; PROVISIONAL"),

    # ── MLB: 1st-inning pitches thrown (Poisson) ─────────────────────────
    # Poisson λ = game-log mean of 1st-inning pitch counts.
    # Ceiling modifications from the first-inning efficiency deterioration
    # score (gate_engine/mlb/first_inning_efficiency.py, Section 18.4) are
    # applied as a post-probability ceiling overlay — they do not change the
    # base model here.  PROVISIONAL until back-tested against settled results.
    ("MLB", "1IP_PITCHES_THROWN"): _prov(
        "mlb_1ip_pitches_poisson_v1",
        ["game_log"],
        "Poisson λ=game-log mean 1st-inning pitch count; "
        "efficiency ceiling from first_inning_efficiency.py Section 18.4; "
        "PROVISIONAL — not yet back-tested against settled results",
    ),

    # ── MLB: Plate Appearances (Section 18.9) ─────────────────────────────
    # Discrete PA opportunity/volume distribution model.
    # Gating and routing performed by gate_engine/mlb/plate_appearances_gate.py.
    # The numeric distribution (P(PA=3/4/5/≥6)) is supplied as pre-computed
    # enrichment; full distribution computation inside the gate engine is a
    # planned future enhancement.  PROVISIONAL until back-tested against
    # settled PA results.
    ("MLB", "MLB_PLATE_APPEARANCES"): _prov(
        "mlb_pa_opportunity_v1",
        ["game_log"],
        "Discrete PA opportunity/volume distribution; gating via "
        "mlb/plate_appearances_gate.py (Section 18.9); numeric distribution "
        "supplied as pre-computed enrichment; PROVISIONAL — not back-tested",
    ),
    # NOTE: ("MLB", "PA") and ("MLB", "PLATE_APPEARANCES") canonical entries are
    # defined above (task #124) with mlb_counting_poisson_v1.  Do not add
    # duplicate keys here — Python dicts use the last definition, which would
    # silently override the task #124 entries.

    # ── NBA: counting stats (Poisson λ = game-log mean) ──────────────────
    # PROVISIONAL: ignores minutes distribution, role changes, opponent context
    ("NBA", "PTS"):          _prov("nba_counting_poisson_v1", ["game_log"]),
    ("NBA", "POINTS"):       _prov("nba_counting_poisson_v1", ["game_log"]),
    ("NBA", "REB"):          _prov("nba_counting_poisson_v1", ["game_log"]),
    ("NBA", "REBOUNDS"):     _prov("nba_counting_poisson_v1", ["game_log"]),
    ("NBA", "AST"):          _prov("nba_counting_poisson_v1", ["game_log"]),
    ("NBA", "ASSISTS"):      _prov("nba_counting_poisson_v1", ["game_log"]),
    ("NBA", "STL"):          _prov("nba_counting_poisson_v1", ["game_log"]),
    ("NBA", "STEALS"):       _prov("nba_counting_poisson_v1", ["game_log"]),
    ("NBA", "BLK"):          _prov("nba_counting_poisson_v1", ["game_log"]),
    ("NBA", "BLOCKS"):       _prov("nba_counting_poisson_v1", ["game_log"]),
    ("NBA", "TOV"):          _prov("nba_counting_poisson_v1", ["game_log"]),
    ("NBA", "TO"):           _prov("nba_counting_poisson_v1", ["game_log"]),
    ("NBA", "TURNOVERS"):    _prov("nba_counting_poisson_v1", ["game_log"]),
    ("NBA", "3PM"):          _prov("nba_counting_poisson_v1", ["game_log"]),
    ("NBA", "FG3M"):         _prov("nba_counting_poisson_v1", ["game_log"]),
    ("NBA", "FTM"):          _prov("nba_counting_poisson_v1", ["game_log"]),
    ("NBA", "PRA"):          _prov("nba_counting_poisson_v1", ["game_log"], "Combo: PTS+REB+AST independent Poisson approximation"),
    ("NBA", "PTS+REB+AST"):  _prov("nba_counting_poisson_v1", ["game_log"], "Combo stat"),
    ("NBA", "PTS+REB"):      _prov("nba_counting_poisson_v1", ["game_log"], "Combo stat"),
    ("NBA", "PTS+AST"):      _prov("nba_counting_poisson_v1", ["game_log"], "Combo stat"),
    ("NBA", "REB+AST"):      _prov("nba_counting_poisson_v1", ["game_log"], "Combo stat"),
    ("NBA", "FPTS"):         _prov("nba_counting_poisson_v1", ["game_log"]),
    ("NBA", "FANTASY"):      _prov("nba_counting_poisson_v1", ["game_log"]),

    # ── WNBA: counting stats ──────────────────────────────────────────────
    ("WNBA", "PTS"):         _prov("wnba_counting_poisson_v1", ["game_log"]),
    ("WNBA", "POINTS"):      _prov("wnba_counting_poisson_v1", ["game_log"]),
    ("WNBA", "REB"):         _prov("wnba_counting_poisson_v1", ["game_log"]),
    ("WNBA", "REBOUNDS"):    _prov("wnba_counting_poisson_v1", ["game_log"]),
    ("WNBA", "AST"):         _prov("wnba_counting_poisson_v1", ["game_log"]),
    ("WNBA", "ASSISTS"):     _prov("wnba_counting_poisson_v1", ["game_log"]),
    ("WNBA", "STL"):         _prov("wnba_counting_poisson_v1", ["game_log"]),
    ("WNBA", "STEALS"):      _prov("wnba_counting_poisson_v1", ["game_log"]),
    ("WNBA", "BLK"):         _prov("wnba_counting_poisson_v1", ["game_log"]),
    ("WNBA", "BLOCKS"):      _prov("wnba_counting_poisson_v1", ["game_log"]),
    ("WNBA", "TOV"):         _prov("wnba_counting_poisson_v1", ["game_log"]),
    ("WNBA", "TO"):          _prov("wnba_counting_poisson_v1", ["game_log"]),
    ("WNBA", "3PM"):         _prov("wnba_counting_poisson_v1", ["game_log"]),
    ("WNBA", "FG3M"):        _prov("wnba_counting_poisson_v1", ["game_log"]),
    ("WNBA", "PRA"):         _prov("wnba_counting_poisson_v1", ["game_log"], "Combo: independent Poisson approximation"),
    ("WNBA", "PTS+REB+AST"): _prov("wnba_counting_poisson_v1", ["game_log"], "Combo stat"),
    ("WNBA", "PTS+REB"):     _prov("wnba_counting_poisson_v1", ["game_log"], "Combo stat"),
    ("WNBA", "PTS+AST"):     _prov("wnba_counting_poisson_v1", ["game_log"], "Combo stat"),
    ("WNBA", "REB+AST"):     _prov("wnba_counting_poisson_v1", ["game_log"], "Combo stat"),
    ("WNBA", "FPTS"):        _prov("wnba_counting_poisson_v1", ["game_log"]),

    # ── NFL: counting stats (Poisson λ = weekly mean) ────────────────────
    # PROVISIONAL: ignores game script, snap count, opponent, weather context
    ("NFL", "PASS_YDS"):        _prov("nfl_counting_poisson_v1", ["game_log"], "Poisson λ=weekly mean passing yards"),
    ("NFL", "PASSING_YARDS"):   _prov("nfl_counting_poisson_v1", ["game_log"]),
    ("NFL", "RUSH_YDS"):        _prov("nfl_counting_poisson_v1", ["game_log"], "Poisson λ=weekly mean rushing yards"),
    ("NFL", "RUSHING_YARDS"):   _prov("nfl_counting_poisson_v1", ["game_log"]),
    ("NFL", "REC_YDS"):         _prov("nfl_counting_poisson_v1", ["game_log"], "Poisson λ=weekly mean receiving yards"),
    ("NFL", "RECEIVING_YARDS"): _prov("nfl_counting_poisson_v1", ["game_log"]),
    ("NFL", "REC"):             _prov("nfl_counting_poisson_v1", ["game_log"], "Poisson λ=weekly mean receptions"),
    ("NFL", "RECEPTIONS"):      _prov("nfl_counting_poisson_v1", ["game_log"]),
    ("NFL", "TARGETS"):         _prov("nfl_counting_poisson_v1", ["game_log"]),
    ("NFL", "PASS_ATT"):        _prov("nfl_counting_poisson_v1", ["game_log"], "Poisson λ=weekly mean pass attempts"),
    ("NFL", "PASS_CMP"):        _prov("nfl_counting_poisson_v1", ["game_log"], "Poisson λ=weekly mean completions"),
    ("NFL", "COMPLETIONS"):     _prov("nfl_counting_poisson_v1", ["game_log"]),
    ("NFL", "SACK"):            _prov("nfl_counting_poisson_v1", ["game_log"]),
    ("NFL", "SACKS"):           _prov("nfl_counting_poisson_v1", ["game_log"]),
    ("NFL", "INT"):             _prov("nfl_counting_poisson_v1", ["game_log"], "Bernoulli/Poisson — low-count event"),
    ("NFL", "INTERCEPTIONS"):   _prov("nfl_counting_poisson_v1", ["game_log"]),
    ("NFL", "FPTS"):            _prov("nfl_counting_poisson_v1", ["game_log"]),
    ("NFL", "FPTS_PPR"):        _prov("nfl_counting_poisson_v1", ["game_log"]),
    # Combo
    ("NFL", "PASS_YDS+RUSH_YDS"):   _prov("nfl_counting_poisson_v1", ["game_log"], "Combo stat"),
    ("NFL", "REC_YDS+RUSH_YDS"):    _prov("nfl_counting_poisson_v1", ["game_log"], "Combo stat"),

    # NFL near-binary TD props (Bernoulli at line ≤ 1.5)
    ("NFL", "TD"):      _prov("nfl_binary_bernoulli_v1", ["game_log"], "Any touchdown scored; Bernoulli hit rate"),
    ("NFL", "PASS_TD"): _prov("nfl_binary_bernoulli_v1", ["game_log"], "Passing TDs; Bernoulli"),
    ("NFL", "RUSH_TD"): _prov("nfl_binary_bernoulli_v1", ["game_log"], "Rushing TDs; Bernoulli"),
    ("NFL", "REC_TD"):  _prov("nfl_binary_bernoulli_v1", ["game_log"], "Receiving TDs; Bernoulli"),
    ("NFL", "ANYTIME_TD"): _prov("nfl_binary_bernoulli_v1", ["game_log"], "Anytime TD scorer; Bernoulli"),
    ("NFL", "TACKLE"):  _prov("nfl_counting_poisson_v1", ["game_log"], "Tackles+assists; Poisson"),
    ("NFL", "KICK_PTS"): _prov("nfl_counting_poisson_v1", ["game_log"], "Kicker fantasy points; Poisson"),

    # ── Fantasy Score composite props ─────────────────────────────────────────
    # All entries are PROVISIONAL + UNVALIDATED until back-tested against settled
    # results.  Formula source: PrizePicks playbook pages (cross-referenced).
    # ⚠ Do NOT use for MONEY_GRADE decisions before validation.
    #
    # NBA / WNBA: PTS×1.0 + REB×1.2 + AST×1.5 + STL×3.0 + BLK×3.0 + TOV×−1.0
    # WNBA: assumed same weights as NBA — confirm from WNBA playbook.
    # NFL: (PassYds/25)+(PassTD×4)+(INT×−2)+(RushYds/10)+(RushTD×6)
    #      +(RecYds/10)+(RecTD×6)+(Rec×0.5 UNCONFIRMED)+(FumLost×−2)
    # MLB-HIT: 1B×3+2B×5+3B×8+HR×10+R×2+RBI×2+BB×2+HBP×2+SB×5
    # MLB-PIT: W×6+QS×4+K×3+Outs×1+ER×−3 (QS=IP≥6, ER≤3)
    ("NBA",  "FANTASY_SCORE"): _prov(
        "nba_fantasy_gaussian_v1", ["game_log"],
        "UNVALIDATED: Gaussian fit over L10 FS series; "
        "formula=PTS×1.0+REB×1.2+AST×1.5+STL×3.0+BLK×3.0+TOV×−1.0; "
        "verify against settled results before money-grade use"
    ),
    ("WNBA", "FANTASY_SCORE"): _prov(
        "wnba_fantasy_gaussian_v1", ["game_log"],
        "UNVALIDATED: same weights as NBA — WNBA_WEIGHTS_ASSUMED_SAME_AS_NBA; "
        "Gaussian fit; verify against PrizePicks WNBA playbook + settled results"
    ),
    ("NFL",  "FANTASY_SCORE"): _prov(
        "nfl_fantasy_gaussian_v1", ["game_log"],
        "UNVALIDATED: NFL_RECEPTION_WEIGHT_UNCONFIRMED (using 0.5 half-PPR); "
        "Gaussian fit over L10 FS series; verify reception weight + settled results"
    ),
    ("MLB",  "FANTASY_SCORE"): _prov(
        "mlb_hitter_fantasy_gaussian_v1", ["game_log"],
        "UNVALIDATED: MLB hitter FS (auto-detect); "
        "formula=1B×3+2B×5+3B×8+HR×10+R×2+RBI×2+BB×2+HBP×2+SB×5; "
        "verify against settled results"
    ),
    ("MLB",  "FANTASY_SCORE_HIT"): _prov(
        "mlb_hitter_fantasy_gaussian_v1", ["game_log"],
        "UNVALIDATED: MLB hitter FS; "
        "formula=1B×3+2B×5+3B×8+HR×10+R×2+RBI×2+BB×2+HBP×2+SB×5"
    ),
    ("MLB",  "FANTASY_SCORE_PIT"): _prov(
        "mlb_pitcher_fantasy_gaussian_v1", ["game_log"],
        "UNVALIDATED: MLB pitcher FS; QS=IP≥6+ER≤3 (derived flag); "
        "formula=W×6+QS×4+K×3+Outs×1+ER×−3; verify against settled results"
    ),

    # ── Tennis: match stats (Gaussian fit over historical match distribution) ─
    # PROVISIONAL: Gaussian λ=match-log mean, σ=match-log std.
    # Coverage limited to ATP/WTA main-draw; ITF/Challenger → NO_REGISTERED_MODEL.
    #
    # ⚠ UNVALIDATED — Fantasy Score formula (games_won + 0.5*aces − 0.5*df) is
    # a best-effort approximation of PrizePicks' proprietary composite.  Exact
    # weights are not published.  Do NOT use for MONEY_GRADE decisions until
    # validated against settled results via the postmortem ledger.  Tag all
    # TENNIS FANTASY_SCORE outputs with flag FANTASY_SCORE_FORMULA_UNVALIDATED.
    ("TENNIS", "FANTASY_SCORE"): _prov(
        "tennis_fantasy_gaussian_v1", ["game_log"],
        "Gaussian fit: games_won + 0.5*aces − 0.5*df (UNVALIDATED APPROXIMATION); ATP/WTA main-draw only"
    ),
    ("TENNIS", "FANTASY"):       _prov("tennis_fantasy_gaussian_v1", ["game_log"]),
    ("TENNIS", "FPTS"):          _prov("tennis_fantasy_gaussian_v1", ["game_log"]),
    ("TENNIS", "GAMES_WON"):     _prov("tennis_gaussian_v1", ["game_log"], "Gaussian; games won per match"),
    ("TENNIS", "GAMES"):         _prov("tennis_gaussian_v1", ["game_log"]),
    # Total Games: exact Markov chain simulation via tennis_total_games_gate.
    # Requires surface/tour context; falls back to historical Gaussian distribution.
    # Three-outcome model (More+Exact+Less=1) for integer lines.
    # Ceiling: MODEL_QUALIFIED_HOLD (PROVISIONAL until settled postmortem validates).
    ("TENNIS", "TOTAL_GAMES"):   _prov(
        "tennis_total_games_markov_v1", ["enrichment"],
        "Markov chain match simulation; surface-adjusted serve baselines; three-outcome contract"
    ),
    ("TENNIS", "ACES"):          _prov("tennis_counting_poisson_v1", ["game_log"], "Poisson; aces per match"),
    ("TENNIS", "DOUBLE_FAULTS"): _prov("tennis_counting_poisson_v1", ["game_log"], "Poisson; double faults per match"),
    ("TENNIS", "DF"):            _prov("tennis_counting_poisson_v1", ["game_log"]),

    # NHL: no registered model — lookup() returns NO_REGISTERED_MODEL
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def lookup(sport: str, stat_key: str, line: float = None) -> dict:
    """
    Return the registry entry for (sport, stat_key).

    For MLB line-gated props:
      - mlb_hits_binomial_pa_v2 only activates for line < 1.0
      - mlb_binary_bernoulli_v1 only activates for line ≤ 1.5
      Lines above those thresholds return NO_REGISTERED_MODEL.

    Never raises.
    """
    sport_u = (sport or "").upper().strip()
    stat_u  = (stat_key or "").upper().strip().replace(" ", "")

    entry = _REGISTRY.get((sport_u, stat_u))
    if entry is not None:
        if line is not None:
            mid = entry.get("model_id", "")
            if mid == "mlb_hits_binomial_pa_v2" and line >= 1.0:
                return dict(_NO_REGISTERED_MODEL)
            if mid == "mlb_binary_bernoulli_v1" and line > 1.5:
                return dict(_NO_REGISTERED_MODEL)
        return dict(entry)

    # Combo-stat fallback: "+" in stat_key for NBA/WNBA/NFL → provisional Poisson
    if "+" in stat_u and sport_u in ("NBA", "WNBA", "NFL"):
        model_id = f"{sport_u.lower()}_counting_poisson_v1"
        return _prov(model_id, ["game_log"], "Combo stat — independent Poisson approximation")

    return dict(_NO_REGISTERED_MODEL)


def is_supported(sport: str, stat_key: str, line: float = None) -> bool:
    """Return True if a registered model (ACTIVE or PROVISIONAL) exists."""
    return lookup(sport, stat_key, line)["status"] != "NO_REGISTERED_MODEL"


def probability_bounds(
    p: float,
    sample_size: int,
    model_status: str,
) -> tuple:
    """
    Return (lower_bound, upper_bound) for a hit probability estimate.

    Heuristic uncertainty band:
      - ACTIVE, n ≥ 10  : ±4%
      - PROVISIONAL, n ≥ 10 : ±8%
      - Any model, n < 10 : ±12%
      - p is None : (None, None)
    """
    if p is None:
        return (None, None)
    if sample_size < 10:
        band = 0.12
    elif model_status == "ACTIVE":
        band = 0.04
    else:
        band = 0.08
    return (round(max(0.0, p - band), 4), round(min(1.0, p + band), 4))
