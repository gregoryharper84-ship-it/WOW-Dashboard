from dataclasses import dataclass

@dataclass(frozen=True)
class PropLeg:
    leg_id: str
    sport: str
    player: str
    event_key: str
    stat_type: str
    direction: str
    line: float

@dataclass(frozen=True)
class CorrelationDecision:
    blocked: bool
    label: str
    shared_thesis_keys: tuple[str, ...]
    reasons: tuple[str, ...]

COMPONENTS = {
    "NBA":        {"POINTS","REBOUNDS","ASSISTS","STEALS","BLOCKS","TURNOVERS"},
    "WNBA":       {"POINTS","REBOUNDS","ASSISTS","STEALS","BLOCKS","TURNOVERS"},
    "NFL":        {"PASSING_YARDS","PASSING_TDS","INTERCEPTIONS","RUSHING_YARDS",
                   "RUSHING_TDS","RECEPTIONS","RECEIVING_YARDS","RECEIVING_TDS","FUMBLES_LOST"},
    "MLB_HITTER": {"HITS","SINGLES","DOUBLES","TRIPLES","HOME_RUNS","RUNS",
                   "RBI","WALKS","HBP","STOLEN_BASES"},
    "MLB_PITCHER":{"PITCHING_OUTS","INNINGS_PITCHED","STRIKEOUTS","EARNED_RUNS",
                   "HITS_ALLOWED","WALKS_ALLOWED","WINS"},
}

def norm(value):
    aliases = {
        "PTS":"POINTS","REB":"REBOUNDS","AST":"ASSISTS",
        "STL":"STEALS","BLK":"BLOCKS","TO":"TURNOVERS","TOV":"TURNOVERS",
        "K":"STRIKEOUTS","ER":"EARNED_RUNS","IP":"INNINGS_PITCHED",
        "HR":"HOME_RUNS","SB":"STOLEN_BASES","BB":"WALKS","REC":"RECEPTIONS",
    }
    value = value.strip().upper().replace(" ", "_")
    return aliases.get(value, value)

class CorrelationGuard:
    def evaluate_pair(self, left, right):
        if left.player.casefold() != right.player.casefold() or left.event_key != right.event_key:
            return CorrelationDecision(False, "PASS", (), ())
        ls, rs = norm(left.stat_type), norm(right.stat_type)
        if "FANTASY_SCORE" not in {ls, rs}:
            return CorrelationDecision(False, "PASS", (), ())
        component = rs if ls == "FANTASY_SCORE" else ls
        family = left.sport.upper()
        if family == "MLB":
            family = "MLB_PITCHER" if component in COMPONENTS["MLB_PITCHER"] else "MLB_HITTER"
        if component in COMPONENTS.get(family, set()) or component == "FANTASY_SCORE":
            key = f"{left.player.casefold()}:{left.event_key}:{family}_FANTASY_SCORE"
            return CorrelationDecision(True, "LEG_REJECT_DUPLICATE_THESIS", (key,),
                ("SAME_PLAYER_COMPONENT_COMPOSITE_OVERLAP",
                 f"{component}_IS_WEIGHTED_IN_FANTASY_SCORE"))
        return CorrelationDecision(False, "PASS", (), ())
