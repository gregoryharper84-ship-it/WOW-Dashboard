# Kalshi Weather V2

Governed analytical weather-market runtime for the separate `WOW_KALSHI_ENGINE` host.

Core architecture:

`Kalshi contract/rules -> exact settlement identity -> authoritative weather evidence -> calibrated probability -> executable market evidence -> fee/edge audit -> deterministic terminal governor -> immutable ledger`

Specialist responsibilities remain separated:
- `ContractSettlementAgent` owns exact contract and settlement identity.
- `WeatherProbabilityAgent` owns independent weather probability validation.
- `MarketCalibrationAuditor` owns executable pricing, calibration and edge validation.
- `KalshiWeatherTerminalGovernor` is deterministic and applies the lowest valid ceiling. It is not an AI voting agent.

Supported implementation families currently include:
- `HOURLY_TEMPERATURE`: end-to-end shadow capture/settlement path with exact hourly rule semantics and weather-index settlement support.
- `DAILY_HIGH_TEMPERATURE`: deterministic evidence fusion is implemented. NWS target-local-date hourly maximum is the primary forecast estimate; official max-so-far is reconstructed from the station observation series; Open-Meteo multi-model highs are disagreement/corroboration inputs. The full daily-high shadow runtime and certified calibration are still required before publication.

Weather evidence hierarchy is contract-aware. The exact Kalshi contract controls settlement authority. NWS/official observations, Open-Meteo, NOAA/NCEI, and optional Xweather are model/evidence inputs according to their governed source roles and cannot override a different contract-named settlement source.

Daily-high date windows are evaluated in the exact contract timezone. This applies both to NWS hourly filtering and Open-Meteo daily aggregation, preventing UTC-midnight drift from changing which observations or forecast hours belong to the contract day.

Market price is never a weather-model feature. Kalshi orderbook evidence is evaluated downstream using executable-side semantics. Missing/stale market evidence may block edge publication without erasing a completed weather probability.

Safety invariants:
- `can_execute=false`
- no order placement/cancel/modify interfaces
- no capital allocation
- no station substitution
- no market-implied-probability substitution
- historical replay must preserve decision-time source vintages
