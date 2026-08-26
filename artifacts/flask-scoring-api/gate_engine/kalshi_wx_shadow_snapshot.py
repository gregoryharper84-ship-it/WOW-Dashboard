"""
gate_engine/kalshi_wx_shadow_snapshot.py
WOW-PATCH-2026-08-08-MULTI-AGENT-KALSHI-WX-SHADOW — Step 10C

Immutable canonical research-snapshot contract for the Kalshi Weather shadow pilot.

WeatherResearchSnapshot carries the complete evidence packet assembled by the
production deterministic fetch path and passed unmodified to all five shadow
research subagents.  Every field is evidence only — no governance authority,
no execution permission, no terminal labels.

IMMUTABILITY
  The dataclass is frozen (frozen=True).  Attempting to set any attribute
  after construction raises dataclasses.FrozenInstanceError.
  List-like fields (source_failures, source_disagreements) are typed as tuple
  to prevent mutation of their contents.
  Dict-like fields (source_timestamps, source_provenance, and the optional
  forecast dicts) are standard Python dicts; callers must treat them as
  read-only — the frozen constraint prevents field reassignment but does not
  deep-freeze the dict contents.

GOVERNANCE BOUNDARY
  No field name matches any key in FORBIDDEN_GOVERNANCE_KEYS from
  gate_engine/kalshi_wx_shadow_schema.py.  A structural test in
  tests/test_kalshi_wx_shadow_snapshot.py enforces this with a static scan
  using the same frozenset.

LIVE-DATA WIRING
  build_test_snapshot() is the only constructor provided here.  It assembles
  a WeatherResearchSnapshot entirely from explicitly-supplied values — no
  network calls, no imports from app.py or the production fetch path.
  Connecting this dataclass to the live NWS/Open-Meteo/NOAA fetch path is
  explicitly deferred to a separate future step.

OUT OF SCOPE
  No Flask routes, no DB imports, no live data fetching, no scoring logic,
  no ceiling resolver calls.
"""
from __future__ import annotations

import dataclasses
from typing import Optional


# ── Immutable snapshot ────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class WeatherResearchSnapshot:
    """
    Immutable evidence packet for one Kalshi Weather shadow research run.

    All fields carry weather evidence only.  No governance authority of any
    kind is expressed here: no terminal_label, no can_execute, no
    capital_allocation, no execution_permission, no trade_authorization.
    This dataclass is frozen: attribute assignment after construction raises
    dataclasses.FrozenInstanceError.

    One instance is created per orchestrator run and passed unchanged to all
    five shadow research subagents — the "one snapshot, same immutable
    evidence to every agent" invariant.
    """

    # ── Identity ──────────────────────────────────────────────────────────────
    research_snapshot_id: str
    """Unique identifier for this snapshot; echoed in all subagent messages."""

    canonical_event_id: str
    """Stable event identifier linking this snapshot to the Kalshi market."""

    # ── Market coordinates ────────────────────────────────────────────────────
    city: str
    """City name for the temperature market (e.g. 'Chicago')."""

    station: str
    """NWS observation station code used for official readings (e.g. 'KMDW')."""

    market_date: str
    """ISO-8601 date (YYYY-MM-DD) of the market."""

    source_cutoff_timestamp: str
    """ISO-8601 datetime at which weather evidence was frozen for this run."""

    # ── Forecast evidence ─────────────────────────────────────────────────────
    nws_gridpoint_forecast: Optional[dict]
    """NWS gridpoint API response or a structured summary; None if unavailable."""

    open_meteo_forecast: Optional[dict]
    """Open-Meteo forecast response or summary; None if unavailable or not used."""

    noaa_ncei_forecast: Optional[dict]
    """NOAA/NCEI forecast data or summary; None if unavailable or not used."""

    official_observations_at_cutoff: Optional[dict]
    """Observed conditions at the source_cutoff_timestamp; None if unavailable."""

    # ── Deterministic model inputs ────────────────────────────────────────────
    forecast_high_used_by_deterministic_model: float
    """Forecast high temperature (°F) actually used by the deterministic pipeline."""

    weather_data_source_tier: str
    """
    Which tier of the source waterfall was actually used.
    e.g. 'NWS_GRIDPOINT', 'OPEN_METEO', 'NOAA_NCEI', 'DEGRADED'.
    """

    forecast_horizon_hours: float
    """Hours between source_cutoff_timestamp and market close."""

    sigma_f: float
    """Forecast standard deviation (°F) used by the deterministic model."""

    deterministic_weather_readiness_state: str
    """
    Readiness state of the deterministic weather pipeline at snapshot time.
    e.g. 'READY', 'DEGRADED', 'UNREADY'.
    """

    # ── Source metadata ───────────────────────────────────────────────────────
    source_timestamps: dict
    """Mapping of source_name -> ISO-8601 timestamp of when that source was fetched."""

    source_provenance: dict
    """Mapping of source_name -> description of how/where it was obtained."""

    source_failures: tuple
    """
    Immutable sequence of strings describing sources that failed or were
    unavailable during the fetch pass.  Empty tuple () if all sources succeeded.
    """

    source_disagreements: tuple
    """
    Immutable sequence of strings describing conflicts between sources.
    Empty tuple () if sources were consistent.
    """


# ── Test / development constructor ────────────────────────────────────────────

def build_test_snapshot(
    *,
    research_snapshot_id: str = "test-snap-001",
    canonical_event_id: str = "event-nyc-20260815",
    city: str = "New York",
    station: str = "KNYC",
    market_date: str = "2026-08-15",
    source_cutoff_timestamp: str = "2026-08-14T12:00:00Z",
    nws_gridpoint_forecast: Optional[dict] = None,
    open_meteo_forecast: Optional[dict] = None,
    noaa_ncei_forecast: Optional[dict] = None,
    official_observations_at_cutoff: Optional[dict] = None,
    forecast_high_used_by_deterministic_model: float = 84.0,
    weather_data_source_tier: str = "NWS_GRIDPOINT",
    forecast_horizon_hours: float = 36.0,
    sigma_f: float = 3.5,
    deterministic_weather_readiness_state: str = "READY",
    source_timestamps: Optional[dict] = None,
    source_provenance: Optional[dict] = None,
    source_failures: tuple = (),
    source_disagreements: tuple = (),
) -> WeatherResearchSnapshot:
    """
    Plain keyword-argument constructor for tests and development.

    All values are supplied explicitly or fall back to safe representative
    defaults.  This constructor performs NO network calls and has NO dependency
    on app.py or the production fetch path.

    Production wiring — connecting WeatherResearchSnapshot to the live
    deterministic NWS/Open-Meteo/NOAA fetch path — is deferred to a
    separate future step.

    Example
    -------
    snap = build_test_snapshot(
        research_snapshot_id="snap-chicago-20260815",
        city="Chicago",
        station="KMDW",
        market_date="2026-08-15",
        forecast_high_used_by_deterministic_model=88.0,
        sigma_f=4.2,
        source_failures=("open_meteo: HTTP 503",),
    )
    """
    return WeatherResearchSnapshot(
        research_snapshot_id=research_snapshot_id,
        canonical_event_id=canonical_event_id,
        city=city,
        station=station,
        market_date=market_date,
        source_cutoff_timestamp=source_cutoff_timestamp,
        nws_gridpoint_forecast=nws_gridpoint_forecast,
        open_meteo_forecast=open_meteo_forecast,
        noaa_ncei_forecast=noaa_ncei_forecast,
        official_observations_at_cutoff=official_observations_at_cutoff,
        forecast_high_used_by_deterministic_model=forecast_high_used_by_deterministic_model,
        weather_data_source_tier=weather_data_source_tier,
        forecast_horizon_hours=forecast_horizon_hours,
        sigma_f=sigma_f,
        deterministic_weather_readiness_state=deterministic_weather_readiness_state,
        source_timestamps=source_timestamps if source_timestamps is not None else {},
        source_provenance=source_provenance if source_provenance is not None else {},
        source_failures=source_failures,
        source_disagreements=source_disagreements,
    )
