# V17 news discovery to research rescore request

**Scope:** follow-on read-only contract for the public GDELT Project collector in PR #2693.
The collector is an independent acquisition step; this bridge **never downloads
publisher articles**, authenticates source claims, changes fitted features, or
invokes a registered scorer.

## Event flow

1. The independently released GDELT discovery job emits a source receipt with
   provider, sport, observed timestamp and discovered article URLs.
2. An *external*, independently authorized verifier must inspect the publisher
   and official fixtures and supply a reviewed-fact record. A GDELT seen date
   is **not** a publisher publication time; an article headline is **not** an
   injury, goalie or lineup fact.
3. `reconcile_news_handoff` requires an exact canonical event ID, canonical
   sport, future kickoff, valid publisher/verification timestamps, source
   freshness and the article's exact discovery URL. It collapses duplicate
   source groups and quarantines conflicting facts. For high-impact facts,
   secondary sources require independent corroboration.
4. `sport_scout_enrichment.enrich_candidate` attaches the resulting
   `news_research_handoff` to an existing team-event or prop candidate
   **only when** `news_discovery_receipt` is supplied. No new independent
   scorer, scheduler or database is introduced.
5. A material reviewed change produces
   `research_status=NEWS_RESEARCH_RESCORE_REQUESTED`, not a model change.
   The controlling model owner must fetch authoritative current inputs,
   rerun the certified market-specific specialist, freeze a new pregame
   model receipt and obtain an independently verified terminal readback.
   The original pregame receipt must remain immutable.

## Explicit safety/acceptance

- Every research handoff has `prediction_authority=false`,
  `model_feature_eligible=false`, `model_rescore_verified=false`,
  `calibrated_probability=null`, `can_execute=false`.
- `research_rescore_requested` is **not** proof that rescoring ran.
- Metadata supplied as `reviewed_facts` is an external assertion. This
  module only checks its consistency; it does not authenticate signatures,
  inspect the publisher URL, or independently establish event truth.
- Post-event discovery, a stale/naive/missing source timestamp, ambiguous
  event, source conflict, unconfirmed high-impact report, or malformed
  source receipt fails closed. Frozen historical features never backfill
  from information first seen post-kickoff.
- Source collector PR #2693 must pass its own independent review and protected
  merge before the feed itself can be called active. A passing unit test for
  this bridge does **not** demonstrate end-to-end real news acquisition or
  fitted scoring.
- Future live acceptance: artifact from the scheduled fetch; authorized
  reviewed event mapping; material feature update; genuine controlling-model
  rescore and immutable pregame receipt; exact deployed SHA; QA and Release
  acceptance; independent model improvement measurement.
- There is no new paid service, database, provider credential, wager execution,
  or synthetic probability. V17_TERMINAL_REDUCER remains the sole terminal
  authority.

Targeted test command:

```bash
cd artifacts/wow-engine
python -m pytest -q test_v17_news_event_research_handoff.py tests/test_v17_sport_scout_teams.py
```

## November 20, 2026 — duplicated headlines are not independent corroboration

Evidence: GDELT source-refresh [run #44717653057](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/actions/runs/44717653057)
returned 15 NFL discovery URLs but only **12 distinct normalized headline texts**.
One identical headline appeared at three publisher URLs and another at two.
These copies are preserved as distinct discovery records, but they must not
be counted as two independent corroborating sources for high-impact news.

The event research handoff therefore requires, **for high-impact facts
supported by secondary/beat sources**, all three: two distinct URLs, two
distinct claimed source groups and at least two distinct **nonempty
normalized discovery headline texts**. Identical headlines despite
punctuation/case changes, or missing headlines, produce
`NEWS_HIGH_IMPACT_CONFIRMATION_REQUIRED` with the auditable conflict
`SYNDICATED_OR_UNATTRIBUTED_HEADLINES`. The original fact rows and URLs remain
in `news_context` for audit; official team/league sources are not newly held
for a missing title.

Distinct headline wording is **not proof of independent reporting**; a wire
story can be rewritten and still depend on one original claim. The external
authorized verifier must establish actual source independence and claim
truth. Neither passing this gate nor `research_rescore_requested=true`
authorizes a fitted model change, calibration update, release or wager.


## November 20, 2026 — receipt provenance chain

The same-run Scout sibling reader already computed a SHA-256 of the **exact
GDELT receipt.json bytes** and recorded it in
`public_news_discovery_bridge.source_sha256`. Event research handoffs now
carry this exact original-byte hash separately as
`source_receipt_sha256`. Direct, non-artifact research calls may lack a raw
hash and explicitly use `null`; they cannot claim an original-byte audit.

The handoff separately records
`discovery_canonical_sha256` over the parsed source receipt,
`discovery_observed_at_utc`, the exact
`event_start_utc` cutoff, `research_observed_at_utc` and
`independently_reviewed_at_utc` on each accepted reviewed fact. These
fields are included in the handoff `receipt_sha256` so changes to the
source bytes, source timestamp, event cutoff, research clock or review
timestamp cannot silently reuse the same digest. Malformed raw source
hashes and unserializable discovery payloads fail closed with their own
research-level status.

**Security and model boundary:** This strengthens provenance of a research
request but does not authenticate reviewer identity, publisher facts,
independent source groups, official event lookup or a fitted scorer. A
different `source_receipt_sha256` string may still be forged by an
untrusted caller; the independent verifier must read the archived raw
artifact and compute its checksum themselves. No new score, model input,
certification or execution authorization is granted. Actual acceptance
requires a certified specialist's new pregame immutable receipt to bind
the permitted verified material feature and corresponding source lineage
at the exact event and model version, with independent readback.


## November 20, 2026 — odds-provider IDs are not official event IDs

The nightly odds discovery adapter currently has a legacy `official_event_id`
field populated from its provider's `/events[].id`. That value is an
**odds-provider event key**, not an independently verified league event ID.
It is retained temporarily for backward-compatible research/market
routing, but every new nightly candidate explicitly also records
`provider_event_id`, `provider_event_source=ODDS_API`,
`canonical_identity_status=PROVIDER_EVENT_ID_UNVERIFIED`,
`canonical_event_id=null`, and
`official_event_identity_verified=false`.

The news research bridge now blocks `NEWS_CANONICAL_EVENT_UNVERIFIED`
when an event carries the explicit unverified status/flag or when an
unverified provider event key equals the claimed official event key.
A copied `official_event_id` and even an altered Boolean cannot establish
independent league identity. All source URLs and candidate rows remain
present; this guard only prevents unverified news-triggered research rescore
requests. Fitted model routes and release authority are untouched.

This is **not** event-identity restoration. To unlock one event, Engineering
must produce independent official fixture validation, an exact league event
ID, immutable source identity and time receipt, plus a distinct provider to
official mapping; SIRT must verify the independent mapping. Do not
automatically equate an odds-provider identifier, a fuzzy team match, or a
market quote with a canonical league ID. The approved verified-ID mapping
must then reach the Scout event candidate before a research rescore request;
an authorized independent reviewed-fact record and actual specialist
rescore are still separate gates.


## November 22, 2026 — NHL first-party identity and byte provenance

**Problem:** The nightly research handoff historically carried an odds/ESPN
provider game key under the legacy `official_event_id` alias. This is not
official NHL identity. The existing `v17.nhl_event_identity` exact-match
resolver had live, independently provided test evidence (November 22) for
Maple Leafs at Islanders (`2026020381`) and Penguins at Stars
(`2026020376`), matching both participants and kickoff at 0-minute drift,
with a strict inclusive 30-minute tolerance. These reported tests do not
prove execution inside the production nightly pipeline.

**Implementation:** Nightly Multi-Scout now calls that *existing* NHL public
season-schedule resolver **once per event**, before constructing the shared
team/prop candidates. A successful future official `FUT`/`PRE` fixture
sets the official NHL game ID and `canonical_event_id`, preserves the
separate odds/ESPN `provider_event_id`, and records exact response-body
`canonical_identity_source_sha256`, source URL, original response-content
byte length and local verification timestamp. The resolver parses the
**same `response.content` bytes it hashes**, rather than hashing
re-serialized JSON. NHL public-source rate limit/transport, unknown team,
wrong opponent, drift, corrupt/missing bytes, started game, or ambiguous
fixture remain typed fail-closed blockers. Unresolved rows stay in the
Scout source candidate inventory, with `official_event_id=null` and the
native error code retained. NHL auto-advance rejects them under that native
code instead of relabeling the failure as `MODEL_UNAVAILABLE`.

**Separate lanes:** A first-party fixture identity does not validate NHL
goalie, lineups, injuries, model artifacts, calibration, market value, or
news claims. The SHA alone is a digest, not archived raw response bytes or
proof of independent reviewer attestation. Preserve the accepted source
receipt and perform an independent pregame readback before any model
publication claim. Do not infer improved model accuracy from a resolved ID.
The team event goes only to the existing LLP team specialist, and player
props only to WOW_PROP_LANE. No automatic wagers or release override;
`can_execute=false` and `V17_TERMINAL_REDUCER` remain unchanged.

**Production acceptance:** An actual nightly run must demonstrate a verified
NHL `canonical_event_id`, distinct provider alias, official source digest
and verification time, with both team and prop routes conserving rows. A
deliberate wrong participant / time must retain its native typed blocker
and prevent a fitted scorer request. Then an independently verified
model-receipt readback is required; green CI or a local resolver invocation
alone does not complete this acceptance.
