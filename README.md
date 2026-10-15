# WOW Betting Engine

Canonical repository for the governed WOW Betting Engine. The active generation
is **V17**; V16 is retained as legacy/superseded material for audit, rollback,
provenance, and regression compatibility.

| Project identity | Current value |
| --- | --- |
| Canonical repository | `WOW-Dashboard` |
| Production stack | Existing Render/Supabase governed stack |
| Primary host | Existing `WOW_BETTING_ENGINE` Custom GPT |
| Team/event capability | Existing `LLP_TEAM_BETTING_ENGINE` Custom GPT |
| Global terminal authority | `V17_TERMINAL_REDUCER` |
| Execution authority | `can_execute=false` |

Live wager and market-order execution is disabled.

## Start here

- [Repository guide](docs/REPOSITORY_GUIDE.md) — codebase map, technologies,
  component boundaries, local checks, and how to read status evidence.
- [Generation status](docs/wow/GENERATION_STATUS.md) — lifecycle authority for
  active V17 versus legacy V16.
- [V17 production status](artifacts/wow-engine/V17_PRODUCTION_STATUS.md) —
  backend, route, editor-sync, and user-journey status, with the evidence each
  status requires.
- [WOW engine README](artifacts/wow-engine/README.md) — engine-specific
  implementation and test history.

V17 is an in-place generation promotion, not a separate product or runtime.
Repository code, CI, and deployment configuration do not by themselves prove
current production deployment or live Custom GPT editor parity; consult the
status documents above for the relevant acceptance evidence.
