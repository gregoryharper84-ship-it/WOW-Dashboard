"""One-shot offline inspector for independent NOAA corroborating evidence.

A deliberately stand-alone, read-only CLI for engineering/SIRT replay. It reads
previously captured bytes and never queries Kalshi or changes live controls.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from .noaa_awc_metar import MetarEvidenceError, parse_metar
    from .noaa_nbm_inventory import NbmEvidenceError, inspect_index
except ImportError:  # running this source file as a direct CLI
    from noaa_awc_metar import MetarEvidenceError, parse_metar
    from noaa_nbm_inventory import NbmEvidenceError, inspect_index

MAX_LOCAL_INPUT_BYTES = 2_000_001


def _read_bounded(path: str) -> bytes:
    with Path(path).open("rb") as f:
        raw = f.read(MAX_LOCAL_INPUT_BYTES + 1)
    if len(raw) > MAX_LOCAL_INPUT_BYTES:
        raise ValueError("EVIDENCE_INPUT_TOO_LARGE")
    return raw


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Kalshi Weather NOAA evidence preflight")
    parser.add_argument("--station", required=True, help="Explicit 4-letter ICAO station; never guessed from a market title")
    parser.add_argument("--as-of", required=True, help="Offset-aware UTC decision/retrieval timestamp")
    parser.add_argument("--metar-json", required=True, help="Captured raw NOAA AWC JSON bytes")
    parser.add_argument("--nbm-index", required=True, help="Captured NOAA NOMADS core GRIB2 .idx bytes")
    parser.add_argument("--nbm-cycle", required=True, help="NBM initial cycle with timezone")
    parser.add_argument("--lead-hours", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        as_of = datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
        cycle = datetime.fromisoformat(args.nbm_cycle.replace("Z", "+00:00"))
        if as_of.tzinfo is None or cycle.tzinfo is None:
            raise ValueError("PROBE_REQUIRES_AWARE_TIMESTAMPS")
        metar = parse_metar(_read_bounded(args.metar_json), station_icao=args.station, as_of=as_of)
        nbm = inspect_index(index_text=_read_bounded(args.nbm_index), cycle_utc=cycle,
                            lead_hours=args.lead_hours, retrieved_at=as_of)
        if nbm.two_meter_temperature_records == 0:
            raise NbmEvidenceError("NBM_TEMP_ELEMENT_ABSENT")
        result = {
            "schema": "WOW_KALSHI_NOAA_RESEARCH_PREFLIGHT_V1",
            "status": "CORROBORATING_EVIDENCE_INSPECTED",
            "retrieved_at": as_of.astimezone(timezone.utc).isoformat(),
            "metar": metar.as_dict(),
            "nbm": nbm.as_dict(),
            "settlement_authority_verified": False,
            "point_in_time_weather_distribution_verified": False,
            "model_probability_available": False,
            "probability_publishable": False,
            "can_execute": False,
        }
    except (MetarEvidenceError, NbmEvidenceError, ValueError, OSError) as exc:
        result = {
            "schema": "WOW_KALSHI_NOAA_RESEARCH_PREFLIGHT_V1",
            "status": "EVIDENCE_REJECTED",
            "hold_code": type(exc).__name__,
            "settlement_authority_verified": False,
            "model_probability_available": False,
            "probability_publishable": False,
            "can_execute": False,
        }
        print(json.dumps(result, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
