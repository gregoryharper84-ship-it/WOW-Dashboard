#!/usr/bin/env python3
"""Strict incident identity reader shared by WOW's existing-PR repair controllers.

Only body lines with exactly one canonical Incident field are accepted. This
avoids constructing an incident/lease from prose, a conflicting second field,
or an untrusted workflow display title. No GitHub writes or model actions.
"""
from __future__ import annotations

import re
import sys

_INCIDENT_LINE = re.compile(
    r"^Incident:[ \t]*(?:#(?P<hash>[1-9][0-9]*)|`(?P<ticks>[1-9][0-9]*)`|(?P<plain>[1-9][0-9]*))[ \t]*$",
    re.MULTILINE,
)


def exact_incident_id(body: str) -> str:
    if not isinstance(body, str):
        raise ValueError("REPAIR_INCIDENT_BODY_INVALID")
    matches = _INCIDENT_LINE.findall(body)
    if len(matches) != 1:
        raise ValueError("REPAIR_INCIDENT_MISSING_OR_AMBIGUOUS")
    return next(value for value in matches[0] if value)


def main() -> int:
    try:
        print(exact_incident_id(sys.stdin.read()))
    except ValueError as e:
        print(f"::error::{e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
