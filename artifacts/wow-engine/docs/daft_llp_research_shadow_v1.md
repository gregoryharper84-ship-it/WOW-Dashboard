# DAFT research + sharp-alert shadow for WOW V17 / LLP

Status: **research-only implementation; not a model, fitted-model update, or production deployment**.
Owner: WOW Research / LLP team-event research; terminal authority remains
`V17_TERMINAL_REDUCER`; `can_execute=false`.

## Inputs and storage boundary

The historical DAFT Discord study and sharp-alert audit dated 2026-10-29 are private
research sources. Do **not** commit their ZIPs, raw alerts, Discord transcripts,
message text, selections, or authenticated scraping output to this public repository.
The only committed tests use synthetic fixture identities, quotes, and narratives.

The two permitted kinds of source intake are (a) VIP picks and recaps (no VIP
Chat) and (b) sharp alerts and updates from U.S. football, NBA/WNBA, MLB, NHL,
soccer, and the older general channel. `normalize_research_record` accepts
`VIP_PICK`, `VIP_RECAP`, `SHARP_ALERT`, `SHARP_UPDATE`, `SHARP_RESULT`.
It refuses to invent missing IDs or capture time. Preserve raw original records,
edit versions, and unrecognized fields in the **private** evidence archive, not
as a trusted or public production row.

The current historical exports have source-display timestamps and source-reported
claims, but do not establish independently attested, immutable, pre-event capture
or complete official contract identities. All historical observations therefore
remain retrospective unless separately independently verified. An alert posted
before the game but captured after the game is not a prospective observation.
`prospective_eligible` requires an independently attested capture, aware publication
and event timestamps, and an exact recognized contract.

Normalize source-reported results separately from independent results. Maintain
`alert_id`, `message_id`, channel, message URL, edit/version digest, publication
time, capture time, quote time, official identity and exact settlement identity.
Deduplicate content versions independently from repeated betting theses.
Review historical data privately; do not use it to manufacture future-only claims.

## Observable sharp-alert process (working reconstruction, not verified code)

The described bot appears to detect relatively fast Pinnacle moves (many around
29–30 minutes), derive a reference fair price, compare another book's lagging
offer, publish a minimum acceptable price/EV/quarter-Kelly suggestion, then
publish a later result and quoted CLV. A price move is **not proof** of sharp
money, betting handle, or profitable actionable follower access. Its private
de-vig method, alternate-line distribution and selection code are unknown.

The research adapter computes independent **proportional normalization** only
when the same book, exact contract, quote timestamp, and all two/three outcomes
are present. It never labels this a certified model probability. Soccer 1X2
requires the draw; regulation ML, draw-no-bet and advancement are different.
Whole-number lines are push-aware, with net return

`p_win * (decimal_odds - 1) - p_loss`

and explicit push mass. Never convert signed spreads or alternative totals
using a generic per-point factor, especially across NFL 3/7. CLV uses only
the exact event, side, period, line and settlement. Quote timestamps must be
observed independently: source posts reportedly updated displayed book quotes
for up to 20 minutes after alert publication.

The captured sharp research contained 835 alert posts, 697 result posts and
690 ID-linked pairs. Of the latter, 406 result posts referred to a different
displayed selected offer than the primary alert. Six draw-related soccer cases
require exact settlement review. Duplicate alert clusters and unmatched IDs
stay in the audit. NPB/KBO have no assumed fitted specialist. Source reported
cumulative results do not equal the visible result subset; the difference
must not be silently "repaired". The NBA headline 119K USD claim is an
illustrative unit conversion, not independent proof of cash profit.

## Using the code

- `v17/daft_external_research.py` handles private-source canonicalization,
  version/thesis dedupe, structured research claims, transparent market math,
  exact-match result and closing-price comparisons, shadow annotations and
  strictly prospective frozen evaluation.
- `v17/daft_llp_research_shadow.py` wraps the *existing* LLP
  `score_team_event_request` after its existing market-context wrapper; no
  source fetch or additional scoring occurs. It creates
  `daft_external_research_shadow.home|away` in the response, without touching
  rank, native blockers, probabilities, calibration, model inputs or reducer.
  It shows `UNAVAILABLE` when no independently usable matching source exists.
- `WOW_DAFT_LLP_SHADOW_ENABLED=true` is the default read-only annotation.
  Setting it to `false` disables the wrapper. This mode is not model approval.
- `WOW_DAFT_PRIVATE_RESEARCH_INDEX=/absolute/path/private/index.json` is an
  optional operator-provisioned read-only JSON array of **sanitized normalized
  prospective records**, loaded once at installation. Keep this file outside
  the Git checkout and outside public static assets with `0600` permissions.
  No private index is bundled or installed by this PR. Missing or invalid input
  means source unavailable, not `MODEL_UNAVAILABLE`.
- No authenticated Discord ingestion, scheduled private feed, quote vendor,
  database schema migration, persistence RPC, or prospectively frozen
  production ledger is installed in this version. The numeric evaluation
  helper accepts independently recorded immutable shadow observations but
  cannot itself supply that evidence.

The annotation is an additive evidence lens only. It never changes
`raw_model_probability`, `calibrated_probability`,
`calibrated_lower_bound`, `probability_publishable`, `rank_eligible`, or
terminal labels. The controlling specialist owns probability; quote/market
research is downstream. Repeated theses are handled in portfolio analysis.

## Promotion criteria

1. Record immutable pre-event captures with independent timestamp attestation,
   exact offers/prices, source versions, independent official outcome, and all
   candidates, not just winners. Separate source outcome and verified outcome.
2. Measure coverage, parser error rates, quote age, follower price availability,
   execution delay and fees, signed-line matching, missing outcomes, dedup and
   source-result disagreements.
3. Evaluate CLV on comparable contracts and net returns at actually obtainable
   quotes, alongside calibration (Brier/log loss and lower-bound reliability)
   only from valid immutable governed probabilities.
4. Compare a frozen LLP baseline with a challenger on chronological,
   event-grouped holdouts; exclude retrospective or leaked observations and
   review counterexamples and thin cohorts.
5. Follow V17 `Discover → Hypothesis → Challenger → Historical Replay →
   Counterexample Review → Holdout/Forward Validation → Regression →
   Recommendation` before proposing model-weight changes. No automatic
   promotion or production betting actions.

## Local tests

From `artifacts/wow-engine`:

```bash
python -m pytest -q tests/test_v17_daft_external_research.py
```

These synthetic tests cover binary/1X2 markets, pushes, signed/alternative
lines, quote freshness, edited versions, retrospective capture, result-line
mismatch, structural reasoning verification, immutable-grade dedupe, and
unchanged LLP probabilities/terminal state. Actual CI and deployed acceptance
must be reported independently. No raw paid research appears in test fixtures.
