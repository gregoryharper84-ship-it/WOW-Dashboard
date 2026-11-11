# WOW V17 Multi-sport Web Intelligence Research Fallback

This changes the NBA-only fallback to **13 explicit sport-specific, research-only
profiles**: NBA, WNBA, NCAAB, NFL, NCAAF, MLB, NHL, soccer, tennis, golf, MMA/UFC,
boxing and cricket. Aliases (PGA, CFB, etc.) resolve to canonical sport keys.

Each profile has five relevant research factors with weights adding to 1.0.
These are **unfitted research priorities**, not sports betting probabilities,
and are not transferable between sports. No scorer registration, calibration,
lower bound, certification, terminal approval, wager, or market EV is created.

Common features:
- One authenticated research scoring endpoint; sport-specific factor validation.
- Exact event/selection/market/line, UTC freshness and independent source group;
  duplicate publisher collapse, disagreement and missing-factor HOLD.
- Sportsbook implied math is downstream-only; never used as sporting evidence.
- Soccer uses explicit three-way 1X2 or draw-no-bet, not a two-way moneyline.
- Each profile explicitly limits market types; unsupported markets fail closed.
- Different sports requiring additional period/settlement verification stay
  research-only and never gain market qualification.

The additional `POST /v17/research/web-intelligence-fallback/from-scout`
adapts **already collected**, provenance-bearing Scout domain evidence for all
profiles. It verifies the official event and selected side of each record, and
requires a recorded directional research impact or favorability; none is
generated from news narrative automatically.

The bridge is **not** a new web crawler. Existing Scout acquisition retains
responsibility for fetching real primary sources and reconciling contradictions.
Source URL provenance is structurally checked but not independently attested
by this scoring endpoint.

Next phase is automated Scout fetch/normalization for each source provider,
then genuine sport-market-specific fitted probability specialists using
historical out-of-time calibration and bounds. Do not label this research-only
milestone as full statistical-model coverage across every sport.

Targeted tests:
`cd artifacts/wow-engine && pytest -q tests/test_v17_web_intelligence_fallback.py tests/test_v17_web_intelligence_multisport.py`

`can_execute=false` always.
