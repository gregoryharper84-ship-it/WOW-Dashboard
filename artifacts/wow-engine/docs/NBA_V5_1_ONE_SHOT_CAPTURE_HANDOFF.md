# NBA one-shot ESPN capture — 2026-10-22

**Status: EXPERIMENT_CREATED / source-only.** This change adds a safe, non-scoring research probe; it does **not** integrate or supersede the reviewed source adapter, V5.1 model, or existing V17 runtime. No deployment, cron, database write or probability publication is authorized.

## One-shot run

On an authorized Render shell with the source branch checked out (or the exact script made available as a separate file), execute **once**:

\`\`\`bash
python3 artifacts/wow-engine/scripts/nba_one_shot_espn_capture.py capture \
  --date 2026-10-22 --outdir /tmp/wow-nba-one-shot-20261022
python3 artifacts/wow-engine/scripts/nba_one_shot_espn_capture.py verify \
  --date 2026-10-22 --outdir /tmp/wow-nba-one-shot-20261022
\`\`\`

The output includes original raw JSON bytes and their SHA-256, source HTTP \`Date\`, local UTC clock, eligible pregame ESPN event IDs, start/status, and skipped-event reasons. A missing \`Date\`, stale clock comparison, redirect, malformed body or duplicate ID fails closed. Re-captures to an existing path fail. **Do not classify any event as a governed probability.**

Use \`pytest -q artifacts/wow-engine/tests/test_nba_one_shot_espn_capture.py\` for mocked no-network regression.

**Evidence limitation:** ESPN's HTTP \`Date\` header and the Render host clock are not independent cryptographic capture-time attestation. Archive raw bytes, capture receipt and Render job identity/timestamp outside the ephemeral shell, then verify hashes against the separately stored bytes. Source bytes, adapter signatures, external clock proof and feature equivalence remain distinct contracts.

## V5 / V5.1 compatibility gap

The V5 signed-forward-shadow acceptance document, \`V5_SIGNED_FORWARD_SHADOW_ACCEPTANCE.md\`, declares ten features:

\`\`\`
home_games_prior, away_games_prior,
home_win_rate_prior, away_win_rate_prior,
home_point_diff_prior, away_point_diff_prior,
home_rest_days, away_rest_days,
home_back_to_back, away_back_to_back
\`\`\`

Current \`artifacts/wow-engine/basketball_team_event_specialist.py\` declares only eight numerical features for \`BASKETBALL_TEAM_EVENT_FEATURES_V2\`, with **capped** rest fields \`home_rest_days_capped\` and \`away_rest_days_capped\`; prior game counts are metadata, not fitted vector entries. Its historical builder consumes all earlier games in the provided corpus without enforcing a 90-day window or excluding preseason by itself. Matching labels do not prove matching historical feature transforms.

The October 22 exploratory ten-feature fit covers 8,747 **retrospectively reconstructed** games and does not prove independent pregame timestamps, certification or superiority over the four-feature challenger. The verified V5.1 ZIP is cataloged in the user Library, but raw bytes were not materializable from this session; exact V5.1 source-level equivalence is unverified.

Before adapter integration: compare exact season/history scope, game counts, point differential definitions, rest caps, back-to-back logic, identity mapping and independent signed feature evidence with the actual V5.1 frozen model. Do not guess missing inputs.

## Recorded gates

| Gate | State |
| --- | --- |
| Local source probe mocked tests | 13 / 13 PASS |
| Real Render ESPN execution | NOT RUN through connected toolset (no one-shot shell/run-job action) |
| V5.1 source-level feature equivalence | UNVERIFIED |
| Independent capture clock / adapter signature | NOT ESTABLISHED |
| Scorer/calibration/production activation | NOT ATTEMPTED |

\`can_execute=false\` and \`probability_publishable=false\` remain invariant. No other model routes or repository governance rules are changed.
