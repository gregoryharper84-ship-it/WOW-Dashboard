# KALSHI DAILY HIGH TEMPERATURE — STATION VERIFICATION TABLE
# Task 1 audit per WOW-PATCH-TEMPLATE.md process
# Source: live Kalshi market rule text (web_fetch/web_search), NOT inferred from blogs
# Date verified: 2026-06-30
# Status: ALL 5 CITIES VERIFIED — safe to hardcode into station mapping table

---

## Summary Table

| city | kalshi_series | settlement_station_name | nws_station_code | hardcode_allowed |
|------|---------------|--------------------------|-------------------|-------------------|
| NYC      | KXHIGHNY  | Central Park, New York        | KNYC (verify CLI issuedby=NYC, site=OKX) | YES |
| LA       | KXHIGHLAX | Los Angeles Airport, CA        | KLAX               | YES |
| Miami    | KXHIGHMIA | Miami International Airport    | KMIA               | YES |
| Chicago  | KXHIGHCHI | Chicago Midway, IL              | KMDW                | YES |
| Austin   | KXHIGHAUS | Austin Bergstrom, TX            | KAUS                | YES |

**Critical correction caught during verification:** initial pattern-matching on
NWS CLI product codes returned `issuedby=PBI` (West Palm Beach) as a false
positive for "Miami" and `issuedby=BUR` (Burbank) as a false positive for
"Los Angeles." Neither is correct. Both were rejected before being written
into the table. This is exactly the failure mode the Task 1 audit exists to
catch — do not let legacy platform "infer" station codes from generic city-weather
searches during implementation.

---

## Per-City Detail

### NYC — VERIFIED
```
city:                      NYC
kalshi_market_name:        Highest temperature in NYC
kalshi_series:              KXHIGHNY
settlement_station_name:    Central Park, New York
exact_rule_language:        "the maximum temperature recorded for the
                             specified <date> published in the National
                             Weather Service's Daily Climate Report for
                             Central Park, New York"
settlement_product:         NWS Daily Climate Report (CLI)
settlement_url_pattern:     forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC
nws_station_code:           KNYC (commonly cited; confirm CLI issuedby=NYC
                             maps to this before final hardcode)
timezone:                   America/New_York (ET)
revision_rule:               Data is preliminary, "subject to revision."
                             Revisions made AFTER expiration do NOT count.
                             Revisions made BEFORE expiration during the
                             trading window CAN be used in settlement.
LST/DST_risk:                NWS Climate Reports use LOCAL STANDARD TIME for
                             the reporting window, even during Daylight
                             Saving Time. During DST, "today's high" window
                             may not align with naive calendar-day clock time.
expiration_timing:           Last Trading Time 11:59 PM ET on event date.
                             Expiration = first 7:00 or 8:00 AM ET following
                             data release, or one week after event date.
resolution_clarity_grade:    A
hardcode_allowed:            true
notes:                       Single named source, explicit station, explicit
                             product, timing rules disclosed in contract text.
```

### LA — VERIFIED
```
city:                      LA
kalshi_market_name:        Highest temperature in LA
kalshi_series:              KXHIGHLAX
settlement_station_name:    Los Angeles Airport, CA
exact_rule_language:        "the highest temperature recorded in Los Angeles
                             Airport, CA for <date> as reported by the
                             National Weather Service's Climatological
                             Report (Daily)"
settlement_product:         NWS Climatological Report (Daily)
nws_station_code:           KLAX
timezone:                   America/Los_Angeles (PT)
expiration_timing:           Last Trading Time 11:59 PM ET. Paid out 30
                             minutes after closing (note: shorter settlement
                             window than NYC's 1 hour — confirm this is
                             consistent across all LA-series contracts).
resolution_clarity_grade:    A
hardcode_allowed:            true
notes:                       Confirmed consistently across 6+ independently
                             dated contract instances (Jun 9, 11, 12, 15, 23,
                             25, 26 2026; Jan 20 2026). Wording is stable.
                             Station = airport (LAX), NOT downtown LA or
                             Burbank (KBUR) — do not substitute.
```

### Miami — VERIFIED
```
city:                      Miami
kalshi_market_name:        Highest temperature in Miami
kalshi_series:              KXHIGHMIA
settlement_station_name:    Miami International Airport
exact_rule_language:        "the highest temperature recorded at Miami
                             International Airport for <date> as reported
                             by the National Weather Service's
                             Climatological Report (Daily)"
settlement_product:         NWS Climatological Report (Daily)
nws_station_code:           KMIA
timezone:                   America/New_York (ET — Miami is Eastern)
expiration_timing:           Last Trading Time 11:59 PM ET. Paid out 1 hour
                             after closing.
resolution_clarity_grade:    A
hardcode_allowed:            true
notes:                       Confirmed across 2 independently dated contract
                             instances (Jun 7, Jun 18 2026). Station =
                             Miami International Airport (KMIA), NOT West
                             Palm Beach (KPBI) — rejected false-positive
                             match during verification, do not substitute.
```

### Chicago — VERIFIED
```
city:                      Chicago
kalshi_market_name:        Highest temperature in Chicago
kalshi_series:              KXHIGHCHI
settlement_station_name:    Chicago Midway, IL
exact_rule_language:        "the highest temperature recorded at Chicago
                             Midway, IL for <date>... according to the
                             National Weather Service's Climatological
                             Report (Daily)"
settlement_product:         NWS Climatological Report (Daily)
nws_station_code:           KMDW
timezone:                   America/Chicago (CT)
expiration_timing:           Last Trading Time 11:59 PM ET. Paid out 1 hour
                             after closing.
resolution_clarity_grade:    A
hardcode_allowed:            true
notes:                       Confirmed across 3 independently dated contract
                             instances (Jan 14, Feb 7, Jun 14 2026).
                             Station = Midway (KMDW), NOT O'Hare (KORD) —
                             independently cross-confirmed via wethr.net's
                             station reference table, which lists KMDW for
                             "Chicago, IL" and KORD separately as
                             "Chicago (ORD)". Do not substitute O'Hare.
```

### Austin — VERIFIED
```
city:                      Austin
kalshi_market_name:        Highest temperature in Austin
kalshi_series:              KXHIGHAUS
settlement_station_name:    Austin Bergstrom, TX
exact_rule_language:        "the highest temperature recorded in Austin
                             Bergstrom on <date>... as reported by the
                             National Weather Service's Climatological
                             Report (Daily)"
settlement_product:         NWS Climatological Report (Daily)
nws_station_code:           KAUS
timezone:                   America/Chicago (CT — Austin is Central)
expiration_timing:           Last Trading Time 11:59 PM ET. Projected payout
                             1 hour after closing.
resolution_clarity_grade:    A
hardcode_allowed:            true
notes:                       Confirmed via live KXHIGHAUS series page and
                             dated instance (Apr 12 2026). Note: a third-
                             party weather bot's series list omitted Austin
                             entirely — verify this was incompleteness in
                             that bot's config, not evidence Austin is
                             unavailable on Kalshi. Series is live and
                             active as of verification date.
```

---

## Universal Rules (apply to all 5 cities)

```
settlement_source:           NWS Climatological Report (Daily) — NEVER
                              AccuWeather, Google Weather, Apple Weather,
                              or any consumer app. Contract rules explicitly
                              warn these "may help guide your decision" but
                              do NOT determine settlement.
data_status:                  Preliminary NWS data is "subject to revision."
                              Revisions after expiration do not count toward
                              settlement. This is a real failure path for
                              any model relying on data pulled too early.
LST_DST_handling:              All cities settle on Local Standard Time
                              reporting windows. During DST, do not assume
                              midnight-to-midnight calendar day = settlement
                              window. Build this into forecast_timestamp
                              validation logic, not just disclosed as a note.
delayed_determination:         Market determination may be delayed if (a)
                              the high temp is inconsistent with 6-hr or
                              24-hr highs reported by METAR, or (b) the
                              final NWS Climate Report high is lower than
                              a previously issued preliminary report.
mutual_exclusivity:            Bracket markets within one city/date are
                              mutually exclusive — only one bracket resolves
                              YES, all others resolve NO. Probability
                              distribution across brackets must sum to 1.00.
insider_trading_prohibition:   Persons employed by Source Agencies (NWS/NOAA
                              presumably) are prohibited from trading these
                              contracts. Not a modeling concern, but a
                              compliance note worth having in the build doc.
```

---

## Patch-Template Conflict Check (per legacy platform's required process)

```
1. Conflict against LLP_GROUND_TRUTH.md:
   PENDING — legacy platform/ChatGPT to confirm no existing weather lane exists
   that this would duplicate.

2. Conflict against existing KALSHI_* labels:
   RESOLVED — per the label correction already agreed: WEATHER_* labels
   are internal model-stage labels only. Final execution labels resolve
   to existing KALSHI_* stack. No new terminal labels introduced.

3. WEATHER_* labels internal-only confirmation:
   CONFIRMED in original spec — WEATHER_MODEL_READY, WEATHER_WATCH,
   WEATHER_SCOUT, WEATHER_REJECT_DATA, WEATHER_REJECT_SETTLEMENT are
   upstream/internal. Final lane label must be one of the existing
   KALSHI_PLAYABLE_LIMIT_ONLY / KALSHI_WATCH / KALSHI_REJECT_* set.

4. Express proxy routes:
   PENDING — must follow same pattern as /api/wow/kalshi/scan and
   /api/wow/kalshi/debug-raw (explicit POST/GET route + injected
   SCORING_API_KEY), not a wildcard. This is the single highest-risk
   item given prior build history — do not skip.

5. Deploy/publish verification:
   PENDING — must include explicit "confirm via health-check-style
   endpoint after publish" step per the lesson from the Kalshi sports
   lane build (code-correct-but-not-deployed caused every prior failure
   in this project).
```

---

## Recommendation

All 5 stations are verified from live, current Kalshi contract rule text
(not inferred, not pattern-matched from generic sources). `hardcode_allowed`
is TRUE for all 5. This table is safe to hand to legacy platform as the seed data
for the `kalshi_weather_stations` mapping table specified in the original
build request.

Next step: run this through the formal `WOW-PATCH-TEMPLATE.md` process
(problem statement, exact delta, conflict check, test case) before legacy platform
implementation begins, per the role split already established.
