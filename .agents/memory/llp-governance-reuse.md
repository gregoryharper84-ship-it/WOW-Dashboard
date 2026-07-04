---
name: LLP governance module must be reused, not duplicated
description: gate_engine/llp_governance.py is the canonical LLP compliance engine (labels + full validator pipeline) — any new LLP escalation/classification feature must route through run_llp_governance, not reinvent label/compliance logic.
---

# LLP governance module must be reused, not duplicated

`gate_engine/llp_governance.py` ("LLP-PATCH-2026-06-27 Execution Governance
v16.1") already defines the `LLPLabel` enum (APPROVED/PLAYABLE/WATCH/SCOUT/
REJECT/CUT) and a full validator pipeline (`run_llp_governance`) covering
price/edge fields, edge threshold by market type, probability cap, timing
freshness, steam protocol, contradiction hard-kills, session exposure,
reapproval rules, and calibration ledger completeness. It is exposed live at
`POST /gate-engine/llp-governance/validate` in `app.py`.

**Why:** a new LLP feature spec independently asked for the same 5 labels and
a "full compliance pass" — building a hand-rolled classifier for this would
have created a second, subtly different compliance system running in
parallel with the tested one, risking divergent accept/reject decisions for
the same bet. Always grep for `llp_governance`/`LLPLabel`/`run_llp_governance`
before adding new LLP label/compliance logic anywhere in `app.py`.

**How to apply:** new LLP flows should build a `candidate` dict in the shape
`run_llp_governance` expects (see `PRICE_EDGE_REQUIRED_FIELDS` /
`CALIBRATION_LEDGER_FIELDS` in `llp_governance.py`) via a pure field-mapping
function, call `run_llp_governance(candidate, session)`, and use
`effective_label` as the terminal label — governance can only cap a
requested label down, never up (`cap_label`). Do not invent a new label
enum or duplicate validator logic.

## `app.py` is unsafe to `import` directly in a test process

Importing `app.py` at module scope (e.g. from a pytest file) hung
indefinitely during this session — it starts background cron threads / DB
connections at import time, not guarded behind `if __name__ == "__main__"`.
To unit-test a specific function's *actual* source without duplicating it,
extract it by AST line range from `app.py` and `exec()` it into an isolated
namespace with only the real dependencies it needs (e.g. `gate_engine`
imports) — this binds the test to the real code while avoiding the import
hang. Prefer this over live-curl-only verification when the logic is pure
(no I/O), and prefer live curl against the running workflow for anything
that touches the odds fetch / DB / cron side of `app.py`.
