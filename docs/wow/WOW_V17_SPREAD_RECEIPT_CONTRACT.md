# V17 governed spread receipt — relation and writer contract

Status: source migration and writer landed, **not applied to production**.
Owner: V17 spread model governance. Applies to the Daily `SPREAD` lane and the
LLP team-intelligence readback.

## Why this exists

The Daily `SPREAD` lane can complete a row from a governed model receipt, but
nothing could store one. Every spread row therefore terminated `HELD` with
`SPREAD_RECEIPT_REGISTRY_UNAVAILABLE:*` or `SPREAD_RECEIPT_NOT_PUBLISHED`. This
document fixes the relation those receipts land in and the only path allowed to
write them.

It does not certify a model, promote a specialist, or enable execution. A stored
receipt is evidence that a fitted model produced a coherent pregame distribution
for one exact line. It is not a wager, an order, or an approval.

## The relation

`public.wow_v17_spread_card_receipts`
(migration: `v17/sql/20261112_v17_spread_card_receipts.sql`)

Append-only. `UPDATE` and `DELETE` are rejected by trigger. RLS is enabled with
no `anon`/`authenticated` policy and `revoke all` on both roles: only governed
service-role server paths reach it.

Enforced by the database, not only by Python:

| Guard | Constraint |
| --- | --- |
| No execution authority | `can_execute = false` |
| Only admitted receipts | `admission_status = 'PASS'` |
| Only fitted, certified models | `model_class = 'CERTIFIED_FITTED'` |
| Only exact-line spread | `market_family = 'POINT_SPREAD'`, side in `HOME`/`AWAY` |
| Coherent distribution | each leg in `[0,1]`, `abs(sum - 1) < 1e-9` |
| Interior bounds | `0 < lower <= p_cover <= upper < 1` |
| Pregame only | `model_timestamp < event_start_time_utc`, `quote_observed_at_utc < event_start_time_utc`, `source_snapshot_timestamp <= model_timestamp` |
| No live/final events | `event_state in ('SCHEDULED','PREGAME')` |
| Pregame score snapshot | `score_snapshot_status = 'PASS'` |
| Terminal provenance | `terminal_authority = 'V17_TERMINAL_REDUCER'`, `controlling_engine_identity = 'LLP_TEAM_BETTING_ENGINE'` |
| Quote provenance present | `line_source`, `quote_source_id`, `quote_sportsbook`, `provider_event_id` all non-null |
| Publishable by construction | `probability_publishable and rank_eligible` |
| Sane line | `-100 < exact_signed_spread < 100`, `home_team <> away_team` |

The last group matters most: a receipt with no observed book quote cannot satisfy
the table, so a synthetic, reconstructed, or implied line has no way in — the
same fail-closed posture the lane already applies on read.

## Writer contract

`v17/spread_receipt_registry.py` is the only sanctioned write boundary.

```python
from v17.spread_receipt_registry import persist_certified_spread_receipt

result = persist_certified_spread_receipt(client, candidate)
```

`persist_certified_spread_receipt`:

1. **Validates** the candidate's structure and timing, raising
   `SpreadReceiptRejected` with a stable `code` (`RECEIPT_DISTRIBUTION_NOT_NORMALIZED`,
   `RECEIPT_MODEL_CLASS_NOT_CERTIFIED`, `RECEIPT_QUOTE_NOT_PREGAME`, …).
2. **Derives** `probability_publishable`, `rank_eligible`, `lower_bound_required`
   and `can_execute` itself. A caller cannot pass a blocked receipt through by
   asserting flags — supplied values are ignored.
3. **Requires admission.** It runs `v17.spread_card_admission` against the
   receipt's own declared side and line and refuses to write anything that is not
   `card_admission_eligible`. A block becomes `RECEIPT_ADMISSION_BLOCKED` with the
   admission blockers attached.
4. **Inserts once.** `upsert(..., on_conflict="receipt_key", ignore_duplicates=True)`.
   Re-persisting the same receipt is a no-op, not a rewrite.
5. **Returns a receipt summary**: `PERSISTED` or `PERSISTENCE_UNAVAILABLE` with
   `SPREAD_RECEIPT_PERSISTENCE_UNAVAILABLE:<ErrorType>`, plus `updates_allowed: false`
   and `can_execute: false`.

There is deliberately **no update, delete, retract, or supersede helper**. A new
line or a re-scored model produces a new row with a new `receipt_key`.

`receipt_key` = sha256 of `sport | event | side | line | model_timestamp | artifact_version`.
`content_sha256` = sha256 of the canonical row, for audit.

### Inputs a caller must supply

Identity and timing (`sport`, `official_event_id`, `event_start_time_utc`,
`home_team`, `away_team`, `exact_line_side`, `exact_signed_spread`,
`settlement_basis`), quote provenance (`line_source`, `quote_source_id`,
`quote_sportsbook`, `quote_observed_at_utc`, `provider_event_id`), model
provenance (`controlling_spread_specialist`, `model_artifact_version`,
`model_class`, `model_version`, `calibration_version`, `source_receipt_id`),
the distribution (`p_cover`, `p_push`, `p_not_cover`), the calibrated bounds, and
the two stamps (`model_timestamp`, `source_snapshot_timestamp`).

### Inputs a caller may not decide

`probability_publishable`, `rank_eligible`, `lower_bound_required`,
`admission_status`, `can_execute`. These are derived or pinned. `can_execute`
must be explicitly `false`; `true` or absent is `RECEIPT_EXECUTION_INVARIANT_UNPROVEN`.

## Producer entrypoint

`v17/spread_receipt_producer.py` is the governed job that fills the relation.

```python
from v17.spread_receipt_producer import run_pregame_spread_production

run = run_pregame_spread_production(
    db,
    sport="NFL",
    events=events,                      # identity + start + teams
    artifact_payload=certified_artifact,  # from the governed model registry
    feature_provider=build_pregame_features,
)
```

`run_pregame_spread_production_from_moneyline` is a thin slate adapter that
projects the Daily `MONEYLINE` row identities into the same call, so a scheduled
job can feed the producer from the slate it already has.

Per event, in order:

1. **Pregame check.** An event at or past its start time is `HELD` on
   `SPREAD_EVENT_ALREADY_STARTED` before any model work — a late receipt is not a
   pregame receipt.
2. **Exact line.** `try_bind_exact_line` reads the append-only ledger only; it
   never collects and never invents a line. A miss is `HELD` with the binder's
   own reason (`SPREAD_EVENT_NOT_FOUND`, `SPREAD_EXACT_LINE_UNAVAILABLE`, …).
3. **Features.** No provider, a schema mismatch, or non-finite values are
   `MODEL_INPUTS_INSUFFICIENT`. The producer has no default vector.
4. **Score.** The certified artifact's own residual distribution produces the
   three-way cover distribution at the ledger line. A throwing scorer is
   `MODEL_SCORER_FAILED`; a malformed one is `MODEL_OUTPUT_INVALID`.
5. **Persist** through `persist_certified_spread_receipt`. A write-boundary
   rejection is `REJECTED` with its code; a persistence failure is `HELD` and
   retryable.

### NFL feature provider

`v17/spread_pregame_features.py` supplies the NFL vector.

```python
from v17.spread_pregame_features import nfl_pregame_feature_provider

run_pregame_spread_production(..., feature_provider=nfl_pregame_feature_provider)
```

It reuses the two governed functions the NFL forward shadow path already uses —
canonical schedule identity, then the leakage-safe P2 pregame feature row — so
the vector has the same family and provenance as the rows the artifact was
fitted on. Inputs are completed prior games, prior team summaries and the
canonical schedule snapshot. The spread line, moneyline price, implied
probability and every other market quantity are absent by construction.

It returns exactly the artifact's feature schema, and refuses with a typed code
otherwise: unresolved canonical identity, `HOME/AWAY_PRIOR_SAMPLE_THIN`,
`PRIOR_RATE_FEATURE_MISSING`, `FEATURE_VECTOR_INCOMPLETE`, any
`TEMPORAL_LEAKAGE_DETECTED` (also re-checked against the newest prior gameday),
non-finite values, a feature the artifact requires but the row does not produce,
or an NBA/WNBA/NCAAF/NCAAB request. The producer surfaces each as
`MODEL_INPUTS_INSUFFICIENT` with that code — never as `MODEL_UNAVAILABLE`, and
never as a substituted vector.

Artifact handling keeps the narrow failure semantics:

| Situation | Code |
| --- | --- |
| No artifact registered for the sport | `MODEL_UNAVAILABLE` |
| Artifact declares `CHALLENGER`/research, or `probability_publishable=false` | `MODEL_UNAVAILABLE` |
| Certified payload missing fields, or widths disagree | `MODEL_OUTPUT_INVALID` |

A challenger is never promoted by this module: it does not certify, does not
fit, and does not rewrite lifecycle state. `MODEL_UNAVAILABLE` here means exactly
what it says — no certified fitted spread capability exists.

Calibrated bounds come from the artifact's own empirical residual sample as a
Wilson cover interval (`EMPIRICAL_RESIDUAL_WILSON_COVER_INTERVAL_V1`, the same
estimator the challenger already reports as its research lower bound, extended
symmetrically). The method is recorded on the receipt payload and in every run
summary so a certifying body ratifies it explicitly rather than inheriting it
silently. A degenerate interval (for example a line that covers on every
residual sample) is rejected by the write boundary instead of publishing 1.0.

The run summary reconciles `rows_in = receipts_persisted + held + rejected` and
reports `can_execute=false`, `dry_run_only=true`,
`automatic_certification=false`, `automatic_promotion=false`, and the
insert-only write scope.

## Read contract

```python
from v17.spread_receipt_registry import read_certified_spread_receipt

receipt, reason = read_certified_spread_receipt(client, sport="NFL", event_id="2026_03_ATL_DAL")
```

Newest row by `model_timestamp` for that event. Failures are typed and never
raise into the lane: `SPREAD_RECEIPT_EVENT_IDENTITY_INCOMPLETE`,
`SPREAD_RECEIPT_REGISTRY_UNAVAILABLE:<ErrorType>`, `SPREAD_RECEIPT_NOT_PUBLISHED`,
`SPREAD_RECEIPT_NOT_ADMITTED`.

`daily_snapshot_runtime.load_certified_spread_receipt` delegates here, so the
writer and the Daily lane share one relation name and one read shape.

**Read-time binding is separate and deliberate.** The stored quote is the quote
the model was scored against. Whether it still matches the append-only market
ledger is decided later, by the lane, which re-runs `spread_card_admission`
against the ledger-bound line and holds on `EXACT_LINE_MISMATCH`. Storing a
receipt never makes a row complete on its own.

## What still has to happen before a real row completes

1. **Apply the migration** through governed release review. Until then the
   relation is absent and every row holds with `SPREAD_RECEIPT_REGISTRY_UNAVAILABLE`.
2. **Certify a spread artifact.** No fitted spread model is currently certified;
   that is a governance decision, not a code change.
3. **Have that producer call `persist_certified_spread_receipt`.** Research and
   shadow artifacts cannot enter the relation (`model_class` gate).
4. **Watch the first admitted receipt** through the Daily lane and the LLP
   readback. Expect `QUALIFIED` on the card with `can_execute=false`,
   `probability_publishable=false` on the LLP card (the card shows evidence; it
   never grants publication).

## Invariants preserved

- `can_execute = false` everywhere, pinned in Python and in the relation.
- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS = true`; nothing here places,
  routes, approves, modifies, or cancels a wager or market order.
- Missing or unverifiable evidence fails closed with a typed reason; no
  probability is invented and no zero-spread default is fabricated.
- `MODEL_UNAVAILABLE` semantics are untouched: this relation stores governed
  output, it does not declare a model available or unavailable.
- No downstream stage erases an upstream blocker: admission blockers, quote
  reasons, and registry reasons all survive to the row and the card.
