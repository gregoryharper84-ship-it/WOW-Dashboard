---
name: External Analyst Intelligence Patch
description: WOW-PATCH-2026-08-08-EXTERNAL-ANALYST-INTELLIGENCE — reusable framework for moneyline research sources (StumpTheSpread first; PickDawgz and others follow same pattern).
---

# External Analyst Intelligence Layer

**Patch ID:** WOW-PATCH-2026-08-08-EXTERNAL-ANALYST-INTELLIGENCE  
**Source role:** DISCOVERY / CONTRADICTION / FAILURE-PATH research only  
**direct_probability_weight = 0.0 ALWAYS and unconditionally**

## Module layout

```
gate_engine/moneyline/external_analyst/
  __init__.py
  types.py               — AnalystOpinion, ThesisTags, ContradictionReport, AnalystIntelligenceResult
  family_resolver.py     — syndication dedup: (source_family, analyst_family, side, date) key
  contradiction_engine.py — analyst vs WOW + analyst vs analyst comparison
  ledger.py              — Postgres primary + JSONL fallback performance ledger
  sources/
    base.py              — ExternalAnalystSourceBase ABC
    stumps_the_spread.py — StumpsTheSpreadAdapter (enrichment-supplied + HTTP fallback)
  orchestrator.py        — main entry: collect → dedup → contradict → ledger → return
```

## Pipeline wiring: Stage 7.5

Inserted AFTER stage 7 (disagreement audit), BEFORE stage 8 (calibration).  
`team_side` / `opponent` hoisted from stage 8 to stage 7.5 to avoid duplication.  
`_market_no_vig_early` computed at 7.5 and reused in stage 8 as `market_no_vig`.

## Governance invariants (all tested)

1. **direct_probability_weight = 0.0** — enforced on every AnalystOpinion and in the orchestrator fetch loop
2. **Analyst picks never enter sport model** — orchestrator returns AnalystIntelligenceResult, not sport model inputs
3. **Unverified claims stay in thesis_tags.unverified_narrative** — verified_factual_claims=[] always from the layer
4. **failure_path_matrix never mutated** — analyst layer has no write path into enrichment
5. **Syndication dedup** — (source_family, analyst_family, side, event_date, team) SHA256 key; second+ copies → is_syndicated_copy=True; contradiction counts use independent only
6. **1 opposing analyst** → EXTERNAL_ANALYST_CONTRADICTION_REVIEW (non-terminal, in _NON_TERMINAL_REVIEW_PREFIXES)
7. **2+ opposing analysts** → force_contradiction_review=True; research_priority=HIGH
8. **Analysts split** → ANALYST_CONSENSUS_UNRESOLVED (non-terminal)
9. **Source failure** → DATA_UNOBTAINABLE; base model unaffected; LAYER_ERROR captured in eai.acquisition_notes
10. **can_execute=False unconditional** in every new module

## Non-terminal review prefixes (pipeline.py)

```python
_NON_TERMINAL_REVIEW_PREFIXES = (
    "TEAMRANKINGS_CONTRADICTION_REVIEW",
    "EXTERNAL_ANALYST_CONTRADICTION_REVIEW",
    "ANALYST_CONSENSUS_UNRESOLVED",
)
```

## Adding a new source

1. Create `gate_engine/moneyline/external_analyst/sources/<name>.py`
2. Subclass `ExternalAnalystSourceBase`, implement `fetch()`
3. Set `source_name` and `source_family` class attributes
4. Register in `orchestrator._SOURCES`
5. Add source_family to `family_resolver.SOURCE_FAMILY_REGISTRY`
6. Tests: same pattern as test_external_analyst_intelligence.py

## Ledger table

`wow_analyst_intelligence_ledger` — Postgres primary, JSONL fallback at  
`gate_engine/data/analyst_intelligence_ledger.jsonl`.  
Rows NEVER rewritten after settlement — only settlement fields filled via `settle_analyst_opinion()`.

## Enrichment contract

GPT operators supply: `enrichment["external_analyst_picks"]["stumps_the_spread"]`  
Accepts a single dict or list of dicts. Required keys: `team`, `side`/`pick`, `reasoning`.  
Optional: `analyst`, `published_at`, `odds`, `market_type`, `event_id`, `favorite_role`.

## Performance ledger metrics supported

Straight-up accuracy, favorite/underdog accuracy, by sport/source/analyst/price-bucket,  
agreement/contradiction vs WOW, closing-market movement direction.  
Promotional source records are never trusted — only reconciled ledger rows count.  
No auto-promotion into probability ensemble; requires future governance patch.
