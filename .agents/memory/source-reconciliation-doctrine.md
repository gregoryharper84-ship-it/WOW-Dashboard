---
name: Source reconciliation doctrine
description: Five hard rules wired into source_grade.py for source priority and reconciliation enforcement (2026-08-03).
---

# Source Reconciliation Doctrine (2026-08-03)

Wired in `gate_engine/source_grade.py` via `_check_reconciliation_rules()`, called per source inside `run()`.

## Five rules

1. **StatMuse / B-grade stat sites** — uncorroborated + role in {l5_l10, exact_stat, game_log} → `RECONCILIATION_REQUIRED:<stype>:needs_official_log`
2. **ESPN blurb** — role in {l5_l10, game_log} → `ESPN_BLURB_STALE_AVERAGES:live_game_log_required`
3. **Odds aggregators** — role == line_price → `EXACT_LINE_AUDIT_REQUIRED:<stype>`
4. **Consumer weather sites** — role == kalshi_weather → `WEATHER_SOURCE_INVALID_FOR_SETTLEMENT:<stype>` + grade downgraded to D
5. **Screenshots (incl. prizepicks_screenshot)** — role == line_price → `LINE_ACTIVE_UNCONFIRMED:<stype>`

## New source types added

- espn_blurb (C), espn_article (C), espn_api (A-)
- odds_aggregator (B), action_network (B), donbest (B), covers (B), vegasinsider (B), thelines (B)
- consumer_weather_site (C), weather_dot_com (C), wunderground (C)
- nws_cli (A), official_weather_station (A)
- prizepicks_screenshot (D), board_capture (D), user_supplied (D)
- web_search (C), news_article (C), official_feed (A)

## New labels in labels.py

- `LINE_ACTIVE_UNCONFIRMED`
- `RECONCILIATION_REQUIRED`
- `EXACT_LINE_AUDIT_REQUIRED`
- `WEATHER_SOURCE_INVALID_FOR_SETTLEMENT`
- `ESPN_BLURB_STALE_AVERAGES`

## Key behavior

Reconciliation blockers are stamped on `row["blockers"]` AND returned in `result["reconciliation_blockers"]`. They apply an additional RESEARCH_INTEREST ceiling when no worse ceiling is already set. They fire even when the source grade alone would pass.

**Why:** Graded ceiling (A/B/C/D) is not enough — a B-grade source like StatMuse passes the ceiling check when corroborated, but still needs reconciliation for exact-stat roles. The doctrine rules are orthogonal to grade ceiling.
