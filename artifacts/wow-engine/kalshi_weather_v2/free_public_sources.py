from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Mapping


@dataclass(frozen=True)
class FreePublicSourceSpec:
    source_id: str
    provider: str
    role: str
    priority: str
    live_default: bool
    commercial_caveat: str | None
    authority_note: str


# This registry is deliberately small and decision-useful. A source is included
# only when it contributes settlement fidelity, independent forecast skill,
# uncertainty measurement, or calibration history. It is not a source-counting
# exercise and no row below can replace the exact settlement source named by a
# frozen Kalshi contract.
FREE_PUBLIC_SOURCE_REGISTRY: Mapping[str, FreePublicSourceSpec] = {
    "KALSHI_PUBLIC": FreePublicSourceSpec(
        source_id="KALSHI_PUBLIC",
        provider="KALSHI",
        role="CONTRACT_MARKET_AND_SETTLEMENT_AUTHORITY",
        priority="A",
        live_default=True,
        commercial_caveat=None,
        authority_note="Exact contract/series rules, market data, and Weather Index settlement evidence.",
    ),
    "NWS_API": FreePublicSourceSpec(
        source_id="NWS_API",
        provider="NOAA_NWS",
        role="PRIMARY_US_FORECAST_AND_OFFICIAL_OBSERVATION",
        priority="B",
        live_default=True,
        commercial_caveat=None,
        authority_note="Primary U.S. forecast/observation evidence; open U.S. government data.",
    ),
    "OPEN_METEO_FORECAST": FreePublicSourceSpec(
        source_id="OPEN_METEO_FORECAST",
        provider="OPEN_METEO",
        role="SECONDARY_MULTI_MODEL_FORECAST",
        priority="C",
        live_default=True,
        commercial_caveat="Free hosted endpoint is non-commercial/rate-limited; use paid or self-hosted endpoint for commercial deployment.",
        authority_note="Secondary model/disagreement evidence only; never settlement authority.",
    ),
    "OPEN_METEO_ENSEMBLE": FreePublicSourceSpec(
        source_id="OPEN_METEO_ENSEMBLE",
        provider="OPEN_METEO",
        role="UNCERTAINTY_ENSEMBLE",
        priority="C",
        live_default=True,
        commercial_caveat="Free hosted endpoint is non-commercial/rate-limited; use paid or self-hosted endpoint for commercial deployment.",
        authority_note="Ensemble spread/uncertainty evidence; not an extra independent provider family when underlying model lineage overlaps.",
    ),
    "OPEN_METEO_ARCHIVES": FreePublicSourceSpec(
        source_id="OPEN_METEO_ARCHIVES",
        provider="OPEN_METEO",
        role="HISTORICAL_FORECAST_REPLAY",
        priority="C",
        live_default=False,
        commercial_caveat="Free hosted endpoint is non-commercial/rate-limited; use paid or self-hosted endpoint for commercial deployment.",
        authority_note="Previous-runs/single-runs archives support decision-time-safe replay; never settlement truth.",
    ),
    "NOAA_NCEI_ADS": FreePublicSourceSpec(
        source_id="NOAA_NCEI_ADS",
        provider="NOAA_NCEI",
        role="HISTORICAL_STATION_CALIBRATION",
        priority="B",
        live_default=False,
        commercial_caveat=None,
        authority_note="Historical station/climate evidence for lanes whose settlement rules name compatible official observations.",
    ),
    "IEM_ASOS": FreePublicSourceSpec(
        source_id="IEM_ASOS",
        provider="IOWA_ENVIRONMENTAL_MESONET",
        role="ASOS_ARCHIVE_CORROBORATION",
        priority="D",
        live_default=False,
        commercial_caveat=None,
        authority_note="Convenient ASOS/METAR archive and one-minute history; corroboration/calibration only unless contract rules explicitly permit it.",
    ),
    "MET_NORWAY_LOCATIONFORECAST": FreePublicSourceSpec(
        source_id="MET_NORWAY_LOCATIONFORECAST",
        provider="MET_NORWAY",
        role="TERTIARY_FORECAST_CORROBORATION",
        priority="D",
        live_default=False,
        commercial_caveat="Must comply with MET Norway identification/licensing terms and send a unique User-Agent.",
        authority_note="Global forecast corroboration only; possible model-lineage overlap means it does not automatically count as an independent provider family.",
    ),
    "NOAA_NOMADS": FreePublicSourceSpec(
        source_id="NOAA_NOMADS",
        provider="NOAA_NCEP",
        role="DIRECT_OPERATIONAL_MODEL_DATA",
        priority="C",
        live_default=False,
        commercial_caveat=None,
        authority_note="Direct HRRR/GFS/NAM/NBM model data. Optional because GRIB parsing adds operational weight and model families are already accessible through governed JSON sources.",
    ),
}


def source_registry_snapshot() -> dict[str, dict]:
    return {key: asdict(spec) for key, spec in FREE_PUBLIC_SOURCE_REGISTRY.items()}


def live_default_source_ids() -> tuple[str, ...]:
    return tuple(key for key, spec in FREE_PUBLIC_SOURCE_REGISTRY.items() if spec.live_default)
