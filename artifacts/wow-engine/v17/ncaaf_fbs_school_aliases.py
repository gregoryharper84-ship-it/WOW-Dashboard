"""Explicit FBS provider display-name aliases for NCAAF settled-history lookup.

Incidents #1636 / #1651: provider (ESPN-style) display names are
"<school> <mascot>" while CFBD settled history is keyed by school name only.
A slate-specific alias list failed every school that was not on the slate it
was written for (Oct 14: Kennesaw State, Missouri State, Western Kentucky,
Sam Houston all returned zero history).

Rules this table enforces:
- Only exact "<school spelling> <mascot>" strings (or an explicit alternate
  school spelling) resolve. No prefix, suffix, or fuzzy inference.
- Every alias maps to exactly one school entry; collisions fail at import.
- An entry may name several CFBD spellings for the same school (renames such
  as "Sam Houston State" -> "Sam Houston"). Resolution still requires exactly
  one of them to exist in the settled history, so a split history fails closed.
- Unlisted names stay unresolved and fail closed with
  SPREAD_FORWARD_HISTORY_INSUFFICIENT.

This module changes identity resolution only. It does not touch features,
fitted coefficients, calibration, lower bounds, or publication gates.
"""
from __future__ import annotations

import unicodedata
from typing import Any, Mapping


def _norm(value: Any) -> str:
    # Same normalization as v17.ncaaf_event_identity._norm (kept local to avoid
    # importing the CFBD client at table-build time).
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(ch for ch in text if ch.isalnum() and not unicodedata.combining(ch))


# (CFBD school spellings, first = preferred) , mascot
# 2026 FBS membership, including 2023-2026 transition schools.
FBS_SCHOOLS: tuple[tuple[tuple[str, ...], str], ...] = (
    # ACC
    (("Boston College",), "Eagles"),
    (("California", "Cal"), "Golden Bears"),
    (("Clemson",), "Tigers"),
    (("Duke",), "Blue Devils"),
    (("Florida State",), "Seminoles"),
    (("Georgia Tech",), "Yellow Jackets"),
    (("Louisville",), "Cardinals"),
    (("Miami",), "Hurricanes"),
    (("NC State", "North Carolina State"), "Wolfpack"),
    (("North Carolina",), "Tar Heels"),
    (("Pittsburgh", "Pitt"), "Panthers"),
    (("SMU",), "Mustangs"),
    (("Stanford",), "Cardinal"),
    (("Syracuse",), "Orange"),
    (("Virginia",), "Cavaliers"),
    (("Virginia Tech",), "Hokies"),
    (("Wake Forest",), "Demon Deacons"),
    # Big Ten
    (("Illinois",), "Fighting Illini"),
    (("Indiana",), "Hoosiers"),
    (("Iowa",), "Hawkeyes"),
    (("Maryland",), "Terrapins"),
    (("Michigan",), "Wolverines"),
    (("Michigan State",), "Spartans"),
    (("Minnesota",), "Golden Gophers"),
    (("Nebraska",), "Cornhuskers"),
    (("Northwestern",), "Wildcats"),
    (("Ohio State",), "Buckeyes"),
    (("Oregon",), "Ducks"),
    (("Penn State",), "Nittany Lions"),
    (("Purdue",), "Boilermakers"),
    (("Rutgers",), "Scarlet Knights"),
    (("UCLA",), "Bruins"),
    (("USC",), "Trojans"),
    (("Washington",), "Huskies"),
    (("Wisconsin",), "Badgers"),
    # Big 12
    (("Arizona",), "Wildcats"),
    (("Arizona State",), "Sun Devils"),
    (("Baylor",), "Bears"),
    (("BYU",), "Cougars"),
    (("Cincinnati",), "Bearcats"),
    (("Colorado",), "Buffaloes"),
    (("Houston",), "Cougars"),
    (("Iowa State",), "Cyclones"),
    (("Kansas",), "Jayhawks"),
    (("Kansas State",), "Wildcats"),
    (("Oklahoma State",), "Cowboys"),
    (("TCU",), "Horned Frogs"),
    (("Texas Tech",), "Red Raiders"),
    (("UCF",), "Knights"),
    (("Utah",), "Utes"),
    (("West Virginia",), "Mountaineers"),
    # SEC
    (("Alabama",), "Crimson Tide"),
    (("Arkansas",), "Razorbacks"),
    (("Auburn",), "Tigers"),
    (("Florida",), "Gators"),
    (("Georgia",), "Bulldogs"),
    (("Kentucky",), "Wildcats"),
    (("LSU",), "Tigers"),
    (("Mississippi State",), "Bulldogs"),
    (("Missouri",), "Tigers"),
    (("Oklahoma",), "Sooners"),
    (("Ole Miss", "Mississippi"), "Rebels"),
    (("South Carolina",), "Gamecocks"),
    (("Tennessee",), "Volunteers"),
    (("Texas",), "Longhorns"),
    (("Texas A&M",), "Aggies"),
    (("Vanderbilt",), "Commodores"),
    # Pac-12
    (("Boise State",), "Broncos"),
    (("Colorado State",), "Rams"),
    (("Fresno State",), "Bulldogs"),
    (("Oregon State",), "Beavers"),
    (("San Diego State",), "Aztecs"),
    (("Texas State",), "Bobcats"),
    (("Utah State",), "Aggies"),
    (("Washington State",), "Cougars"),
    # Mountain West
    (("Air Force",), "Falcons"),
    (("Hawai'i", "Hawaii", "Hawaiʻi"), "Rainbow Warriors"),
    (("Nevada",), "Wolf Pack"),
    (("New Mexico",), "Lobos"),
    (("Northern Illinois",), "Huskies"),
    (("San José State", "San Jose State"), "Spartans"),
    (("UNLV",), "Rebels"),
    (("UTEP",), "Miners"),
    (("Wyoming",), "Cowboys"),
    # American
    (("Army",), "Black Knights"),
    (("Charlotte",), "49ers"),
    (("East Carolina",), "Pirates"),
    (("Florida Atlantic",), "Owls"),
    (("Memphis",), "Tigers"),
    (("Navy",), "Midshipmen"),
    (("North Texas",), "Mean Green"),
    (("Rice",), "Owls"),
    (("South Florida",), "Bulls"),
    (("Temple",), "Owls"),
    (("Tulane",), "Green Wave"),
    (("Tulsa",), "Golden Hurricane"),
    (("UAB",), "Blazers"),
    (("UTSA",), "Roadrunners"),
    # Sun Belt
    (("Appalachian State", "App State"), "Mountaineers"),
    (("Arkansas State",), "Red Wolves"),
    (("Coastal Carolina",), "Chanticleers"),
    (("Georgia Southern",), "Eagles"),
    (("Georgia State",), "Panthers"),
    (("James Madison",), "Dukes"),
    (("Louisiana", "Louisiana Lafayette"), "Ragin' Cajuns"),
    (("Louisiana Tech",), "Bulldogs"),
    (("Marshall",), "Thundering Herd"),
    (("Old Dominion",), "Monarchs"),
    (("South Alabama",), "Jaguars"),
    (("Southern Mississippi", "Southern Miss"), "Golden Eagles"),
    (("Troy",), "Trojans"),
    (("UL Monroe", "Louisiana Monroe"), "Warhawks"),
    # Conference USA
    (("Delaware",), "Blue Hens"),
    (("Florida International", "FIU"), "Panthers"),
    (("Jacksonville State",), "Gamecocks"),
    (("Kennesaw State",), "Owls"),
    (("Liberty",), "Flames"),
    (("Middle Tennessee",), "Blue Raiders"),
    (("Missouri State",), "Bears"),
    (("New Mexico State",), "Aggies"),
    (("Sam Houston", "Sam Houston State"), "Bearkats"),
    (("Western Kentucky",), "Hilltoppers"),
    # MAC
    (("Akron",), "Zips"),
    (("Ball State",), "Cardinals"),
    (("Bowling Green",), "Falcons"),
    (("Buffalo",), "Bulls"),
    (("Central Michigan",), "Chippewas"),
    (("Eastern Michigan",), "Eagles"),
    (("Kent State",), "Golden Flashes"),
    (("Massachusetts", "UMass"), "Minutemen"),
    (("Miami (OH)",), "RedHawks"),
    (("Ohio",), "Bobcats"),
    (("Sacramento State",), "Hornets"),
    (("Toledo",), "Rockets"),
    (("Western Michigan",), "Broncos"),
    # Independents
    (("Connecticut", "UConn"), "Huskies"),
    (("Notre Dame",), "Fighting Irish"),
)


class FBSAliasTableError(ValueError):
    """Raised at import when the alias table is not collision-free."""


def _build() -> tuple[dict[str, str], dict[str, tuple[str, ...]]]:
    """Return (alias_norm -> preferred school, preferred_norm -> all spellings)."""
    aliases: dict[str, str] = {}
    spellings: dict[str, tuple[str, ...]] = {}
    school_norms: dict[str, str] = {}
    for names, mascot in FBS_SCHOOLS:
        preferred = names[0]
        key = _norm(preferred)
        if key in spellings:
            raise FBSAliasTableError(f"duplicate school entry: {preferred}")
        spellings[key] = tuple(dict.fromkeys(names))
        for name in names:
            n = _norm(name)
            owner = school_norms.setdefault(n, key)
            if owner != key:
                raise FBSAliasTableError(f"school spelling {name!r} claimed by two entries")
    for names, mascot in FBS_SCHOOLS:
        preferred = names[0]
        key = _norm(preferred)
        candidates = {_norm(f"{name} {mascot}") for name in names}
        # Alternate bare spellings ("App State", "UConn") identify the school.
        candidates |= {_norm(name) for name in names[1:]}
        for alias in candidates:
            if alias in school_norms and school_norms[alias] != key:
                raise FBSAliasTableError(f"alias {alias!r} is another school's name")
            owner = aliases.setdefault(alias, preferred)
            if _norm(owner) != key:
                raise FBSAliasTableError(f"alias {alias!r} maps to two schools")
    return aliases, spellings


FBS_DISPLAY_ALIASES, FBS_SCHOOL_SPELLINGS = _build()


def merge_verified_aliases(
    base: dict[str, str], extra: Mapping[str, str]
) -> dict[str, str]:
    """Merge alias maps; the same alias must never name two different schools."""
    merged = dict(base)
    for alias, school in extra.items():
        prior = merged.setdefault(alias, school)
        if _norm(prior) != _norm(school):
            raise FBSAliasTableError(
                f"alias {alias!r} conflicts: {prior!r} vs {school!r}"
            )
    return merged


def school_spellings(school: str) -> tuple[str, ...]:
    """All CFBD spellings of one school (just the input when not tabled)."""
    return FBS_SCHOOL_SPELLINGS.get(_norm(school), (school,))


__all__ = [
    "FBS_SCHOOLS",
    "FBS_DISPLAY_ALIASES",
    "FBS_SCHOOL_SPELLINGS",
    "FBSAliasTableError",
    "merge_verified_aliases",
    "school_spellings",
]
