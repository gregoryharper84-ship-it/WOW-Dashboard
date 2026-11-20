# NotebookLM dedicated knowledge base — Gemini Challenger & Kalshi/WOW

**Proposed notebook title:** WOW + Kalshi — Gemini Independent Intelligence (2026)
**Creation status:** must be created in the authorized user's NotebookLM
account; this file alone does not create a Google notebook.

## Scope and priorities

The notebook provides grounded **research context**, not authoritative live
market data, code execution, or a synchronized production source of truth.

### Authoritative references to add individually

1. Current Kalshi Weather Market Expert Skill v1.4 source, preserving the
   exact complete file; do not treat notebook summaries as controlling.
2. Current WOW engineering governance / AGENTS.md, including
   V17_TERMINAL_REDUCER, can_execute=false, release gates and SIRT independence.
3. Existing WOW worker runtime architecture and schema (repository paths).
4. GitHub issue #1739 and related verified lease implementation evidence.
5. Gemini pilot task/receipt contracts and the GEMINI_HANDOFF.md packet.
6. Current official Kalshi settlement rule snapshots for the 20 target
   contracts, each with ticker, URL, snapshot hash and availability time.
7. Designated settlement publication and station metadata for each contract.
8. NOAA/NWS model and observation sources with issue and availability times.
9. Google WeatherNext official specifications and licensing/access terms,
   plus model output documentation once actually granted.
10. Pilot run outcomes and SIRT-reviewed error attribution, uploaded only
    after privacy and evidence hygiene checks.

### Notebook operating instructions

* Answer exclusively from the notebook's uploaded sources when reviewing
  previously established requirements. Flag absent material, do not improvise.
* Report source title, as-of timestamp and exact cited paragraph/section.
* Source authority: Kalshi rules > named settlement publications >
  NWS/NOAA > reproducible forecast models > secondary forecasts.
* Notebook contents may become stale; actual contract, price, observation,
  repository, CI and deployment state must come from newly verified sources.
* Report weather uncertainty separately from settlement uncertainty.
* Do not infer Kalshi P(YES) from forecast labels or market prices.
* Do not suggest code was merged, tested or deployed without receipts.
* Never upload provider keys, GitHub App private keys, Supabase service role
  tokens, Render secrets, customer personal data, or unredacted private logs.
* Do not grant NotebookLM collaborators production credentials.

### Seed research questions

1. For a specific contract snapshot, what exact evidence supports each
   settlement term and what remains ambiguous?
2. What can make an observed station maximum differ from the settlement
   publication, given the named authority?
3. What later publication or correction might change the settlement result?
4. Which information was available at each decision timestamp?
5. Is a proposed weather model input independent of market price?
6. What regression cases threaten same-incident and cross-incident leases?
7. Which existing WOW code paths have not been fenced before GitHub mutation?

### Recommended notebook organization

One dedicated cross-ecosystem notebook for architectural handoff and
research source catalog. Maintain separate Kalshi contract-specific evidence
bundles and WOW defect-specific evidence bundles outside the notebook so
high-churn data do not become silently stale. Attach finalized summary
documents to the notebook after independent evidence checks.

### NotebookLM setup check

Create a notebook in the authorized Google account; import this document,
GEMINI_HANDOFF.md and the Kalshi v1.4 skill as sources; confirm all three
appear with correct source titles; ask NotebookLM a question whose answer
appears verbatim in one source and verify citation; test a question with
absent evidence and ensure it says unknown; capture the notebook URL/ID
in an approved internal registry. Notebook creation and live source syncing
remain UNVERIFIED until the user or authorized NotebookLM integration does it.
