# Kalshi Weather V2 live-source contract

The analytical runtime is GET-only for external evidence and market data.

Authoritative/primary sources:
- exact live Kalshi contract rules determine settlement authority
- NWS API supplies U.S. point/grid/hourly forecast and official station observations where applicable
- NOAA/NCEI supplies historical station/climate data for calibration/reconciliation
- Open-Meteo supplies secondary multi-model and archived-run evidence for disagreement and lead-time skill
- Xweather is optional corroboration only and must never be a hard dependency

Market data:
- Kalshi public market and orderbook GET endpoints only
- orderbook is bid-only; executable YES buy is derived from best NO bid as 1-NO_bid, and executable NO buy from best YES bid as 1-YES_bid
- midpoint/displayed chance is never treated as executable price
- fee/friction schedule must be separately verified before edge publication

Safety:
- no order creation, modification, cancellation, portfolio or transfer methods are implemented
- market price cannot feed the weather probability model
- can_execute=false in every terminal state
