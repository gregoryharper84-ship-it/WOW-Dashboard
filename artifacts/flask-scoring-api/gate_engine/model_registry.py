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

    # NFL and NHL: no registered model — lookup() returns NO_REGISTERED_MODEL
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

    # Combo-stat fallback: "+" in stat_key for NBA/WNBA → provisional Poisson
    if "+" in stat_u and sport_u in ("NBA", "WNBA"):
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
