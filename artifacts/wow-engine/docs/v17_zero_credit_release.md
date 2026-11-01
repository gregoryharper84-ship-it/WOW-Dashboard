# V17 zero-credit deterministic review lane

This is an opt-in **reduced-assurance** mode. It is not an AI review, human
review, autonomous SIRT semantic review, or a source-code correctness proof.
There are **no OpenAI/Claude API calls**. A PASS establishes only the specified
exact-head deterministic policy and regression checks.

## Always-on no-API-credit evidence (proposed CodeQL PR rollout)

The existing `.github/workflows/wow-codeql.yml` analyzes Python and
JavaScript/TypeScript on `pull_request` to `main`, `push` to `main`,
the existing daily schedule, and manual dispatch. Standard GitHub-hosted Actions
for a public repository do not need OpenAI, Claude, Gemini, Cursor or a billing
API key. CodeQL executes third-party static queries and publishes findings in
GitHub code scanning; it does **not** review the full semantics of a change,
and a successful analyzer process is not proof of zero security findings.

Do not create a second release authority, automatically approve a protected
change on the basis of CodeQL, or remove the existing exact-head required
regression, negative, concurrency, provenance and fail-closed tests.
CodeQL is additional free evidence until independently accepted governance
changes explicitly define how it factors into protected release admission.

OpenAI Codex can optionally be used for coding and PR review by signing in
with an existing ChatGPT plan. This does not require an OpenAI API key,
but **Codex plan usage has limits** and is **not** a free unlimited autonomous
release-signing authority. Do not run Codex using an API key in this zero-credit
lane or treat a Codex finding/absence of findings as an independent QA App PASS.

## Enable only after approved protected merge

```bash
REPO=gregoryharper84-ship-it/WOW-Dashboard
gh variable set WOW_QA_INDEPENDENT_REVIEW_ACTIVE -R "$REPO" --body false
gh variable set WOW_QA_ZERO_CREDIT_MODE -R "$REPO" --body true
```

Both flags true fail closed; no silent fallback from API quota failures. This
is a **repository-wide governance policy change**, not PR-specific. Review
scope and reduced assurance must be accepted before activation.

The QA and Release jobs check out protected main and do not execute PR code.
The QA check is authored only through QA App (5245832); Release uses a
distinct Release App (5245853). Release independently recomputes QA findings,
verifies current PR head and source QA policy receipt and validates the QA
App check on the same SHA. A stale, missing, foreign or failed required
regression remains HOLD. The three protected CI checks and change-impact
gate are not removed.

Trust-root changes (.github/, .agents/, governance scripts and modules)
still require non-owner authorship and authenticated owner exact-head
approval; owner-authored trust roots fail closed. No human or semantic code
review is *asserted* by this mode. The old separate source-review certifier
may still be red; it must not be misreported as a successful independent
review, even if it is not required by the five-check ruleset.

The opt-in workflow dispatcher is a transport only and may never mint an
approval, QA check, Release check, semantic review receipt, deployment,
wager or model probability. It needs its own separate activation once its
routing and tests are verified.

Before declaring NFL restoration, require five protected checks at the
same head, an authorized protected merge, Render LIVE on the merged SHA,
and real persisted and reconciled NFL final-refresh scoring receipts.
can_execute=false always.
