# WOW V17 Kalshi Slip Research Exchange — LLP and WOW Betting Engine

Status: feature-gated research-only backend candidate, not deployed or independently accepted. No actual GPT editor access has been verified.

## Purpose

LLP_TEAM_BETTING_ENGINE (team/event intelligence) and WOW_BETTING_ENGINE (player/prop intelligence) share one non-authoritative research ledger. Either can submit a typed finding and either can read the observations of both. This is NOT automatic learning, scoring, probability publication, Kalshi order synchronization, staking, or terminal authority.

LLP observations -> authenticated research exchange <- WOW observations
                                        |
                               append-only Supabase ledger
                                        |
                          both hosts consume via bounded GET
                                        |
                         independent verification required
                                        |
                           existing V17 model feedback lanes

## API

POST /v17/intelligence/kalshi-slip-exchange
GET /v17/intelligence/kalshi-slip-exchange?consumer_host=WOW_BETTING_ENGINE&scope=EVENT&reference_id=...
GET /v17/intelligence/kalshi-slip-exchange?consumer_host=LLP_TEAM_BETTING_ENGINE&scope=EVENT&reference_id=...

The POST accepts only source_host, scope (EVENT or COHORT), sport, reference_id, finding_type, finding_code, analysis_stage, as_of_utc, source_observed_at_utc, evidence_refs and optional market_ref/event_start_utc. No free-form personal financial data, account/order identifiers, money, probabilities, predictions, or approval labels. A source reference is a claimed pointer, not proof of its authenticity.

All writes and reads return model_feature_eligible=false, probability_publishable=false, can_execute=false. Source and consumer host identities are claimed, not independently authenticated, because the current shared bearer establishes API access but not which Custom GPT is calling.

## Install and release

Production mounts the installer only when the V17 active guard is true AND environment flag WOW_V17_KALSHI_SLIP_EXCHANGE_ENABLED=1. It is OFF by default. Apply the SQL migration through governed deployment before enabling the flag. Server-side service-role insert/select only; RLS blocks public, anon and authenticated database roles; immutable trigger rejects update/delete. On persistent failure, API returns 503. Neither code nor flag grants order or capital authority.

After CI and independent QA/release acceptance, separately integrate the operations into the canonical single-domain Action layouts for BOTH GPT hosts without violating editor instruction/operation constraints. Verify actual live editors with fresh-chat auth and GET/POST canaries; do not claim access from repository changes alone.

## Example finding

{
  "source_host":"LLP_TEAM_BETTING_ENGINE",
  "scope":"EVENT",
  "sport":"NBA",
  "reference_id":"official-event-id",
  "market_ref":"public-market-ref",
  "finding_type":"SCHEDULE_REST_TRAVEL",
  "finding_code":"ROAD_BACK_TO_BACK_OBSERVED",
  "analysis_stage":"PREGAME_RESEARCH",
  "as_of_utc":"2026-11-14T18:00:00Z",
  "source_observed_at_utc":"2026-11-14T17:00:00Z",
  "event_start_utc":"2026-11-15T00:00:00Z",
  "evidence_refs":["official-schedule-receipt"]
}

The WOW Betting Engine can submit another supported finding type under source_host WOW_BETTING_ENGINE. Both can read the shared set, but no finding automatically changes the forecast.

## Kalshi history provenance

The eight visible settled combinations on November 14, 2026 had displayed cost $300.87, payout $106.35 and net -$194.52. Their dates, exact legs and failure causes remain unverified. An aggregate COHORT research finding can reference this retrospective without inventing pregame model receipts or causes. Link official underlying selections and immutable pregame inputs before any genuine attribution or calibration.

## Acceptance checklist

1. Pass independent tests, security review, immutable-ledger and 401/422/503 cases.
2. Apply schema and verify RLS / append-only restrictions with actual role-scoped integration tests.
3. Confirm deployed SHA, feature flag and both engine-originating GET/POST live canaries.
4. Update and attest each canonical Action/editor. Verify source host identity separately if it must become trusted.
5. Only promote independently proven feature/settlement improvements through existing V17 specialist fitting, calibration, forward backtesting and terminal governance.

Absolute invariants: can_execute=false; capital_allocation=false; no live orders; no automatic promotion or trusted probability from research evidence; Kalshi recovery stays active.
