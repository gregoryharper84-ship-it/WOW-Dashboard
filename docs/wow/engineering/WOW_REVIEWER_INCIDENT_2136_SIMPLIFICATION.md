# Reviewer incident #2136 — collapse to one evidence requirement and two change classes

Status: proposed governance decision. It grants no merge and changes no workflow,
workflow permission, ruleset, credential or required check. It takes effect only
when the owner merges it; merging a document is the authorization act, because
`docs/` is not a trust root (`TRUST_ROOT_PREFIXES = (".github/", ".agents/", ".claude/")`).

## 1. What is actually blocking, verified in code

| Layer | Current behaviour | Load-bearing for a trust-root change? |
| --- | --- | --- |
| Semantic reviewer (`wow-v17-simple-security-review.yml`) | Claude -> Gemini; every run currently ends `HOLD: HTTP_404`, `Reviewer: none` | **Yes.** It is the only reviewer that accepts owner-authored trust-root source, and it has no endpoint attribution, so the failing route is unknown |
| Owner fallback (third stage) | requires Engineering App author **and** non-owner original author | No — it is structurally unavailable for owner-origin source (`OWNER_IS_SOURCE_AUTHOR_OR_IDENTITY_UNVERIFIED`) |
| Independent QA (`wow-v17-independent-qa-check`) | `QA_TRUST_ROOT_OWNER_AUTHORED` fires unconditionally for owner-authored trust-root changes; non-owner source needs the owner's `APPROVED` review at the exact head | Not required by ruleset, and unapprovable for owner-authored source |
| QA canary (OpenAI) | additionally rejects `QA_REVIEW_CANARY_OWNER_AUTHORED` **and** `QA_REVIEW_CANARY_TRUST_ROOT`; under `WOW_QA_ZERO_CREDIT_MODE=true` the review step does not run at all | No |
| Engineering App adoption | three pending `exact-replay-*` PR cap; copies do not create authorship independence | No for diagnostics; optional otherwise |
| Ruleset #24907422 | requires exactly `WOW required-three regression`, `WOW governed probability backend`, `WOW additional required regression`, App `15368`; zero required approvals; no bypass actors | Merge gate, already satisfied |

So the stack has five layers and exactly one unmet requirement: **a genuine provider
verdict**. Every other layer of the reviewer chain is either not required, inert under
current policy, or unapprovable for owner-authored trust-root source. Treating all five
as simultaneous prerequisites is what turns one missing credential into a total stop.

## 2. The collapse

Keep two things:

1. **Merge requirement** — the three pinned CI checks in ruleset #24907422. Unchanged.
2. **One security-evidence requirement** — a genuine independent semantic verdict, or a
   typed `HOLD` plus owner attention. An owner approval is never a verdict.

Remove from the *critical path* (without deleting any code): the OpenAI canary, the
zero-credit evidence path, the QA/Release App checks as approval evidence for trust-root
changes, and the adoption queue as a precondition for a diagnostic repair. Each remains
available and each remains honest about what it proved.

## 3. Two change classes

**Class D — diagnostic-only.** A change that cannot alter any decision. It qualifies only
if all of the following hold:

- the diff adds or changes diagnostic strings, log lines, comments or test assertions;
- it introduces no new `decision`, `conclusion`, `PASS`, `REJECT`, `ProviderUnavailable`
  or receipt field, and reclassifies no HTTP status;
- it adds no endpoint, retry, fallback, approval, permission, secret or trigger;
- the reviewer's existing suite passes unchanged, and the diff carries a test that
  asserts the pre-existing classification is untouched.

Evidence required: the three pinned CI checks, the owner's explicit authorization recorded
in the incident, and a one-line statement of why the diff cannot approve. No semantic
verdict, no App copy, no new reviewer.

**Class S — semantic.** Everything else, including any change to classification, fallback,
escalation, permissions, receipt shape, or the trust-root detection itself. Requires a
genuine provider verdict on the exact head; owner approval is not available for
owner-authored source, and an Engineering App copy of owner source is not independence.
This class stays blocked until a provider route returns a verdict — that is the correct
outcome, not a defect.

## 4. What neither class authorizes

No self-approval, fabricated review, App-identified copy of owner source, force-merge,
removal or weakening of a required check, new required check, new control plane, new
recurring certifier, new mandatory human, raised adoption cap, relabelling of a bare 404
as quota/model unavailability, or any change to `can_execute=false` and
`DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`.

## 5. Re-entry and expiry

Class D exists only while no provider route can produce a verdict. When one returns a
verdict, Class D is retired, every subsequent trust-root change is Class S, and the
provider chain is repaired and hardened under that review. The adoption cap and the
QA/Release App checks are unchanged; docs step 4 of `docs/WOW_GOVERNANCE_V2.md` still
governs when the security check becomes a pinned requirement.

Residual failures are recorded, not closed: the 404's own endpoint, the recovery PRs
awaiting semantic review, and the fact that the reviewer currently cannot repair itself
because the only QA-acceptable trust-root route (Engineering App re-proposal plus an
owner exact-head approval) is exactly the route the semantic reviewer rejects as
laundering.
