---
name: Kalshi WX terminal-label fail-closed guard
description: WOW-PATCH-2026-08-08 — registry + validation guard on Kalshi Weather terminal labels; applied at both route call sites in app.py.
---

## Rule
`_KALSHI_WX_TERMINAL_LABEL_REGISTRY` (frozenset, 6 members) and `_validate_wx_terminal_label(label: str) -> bool` live in `app.py`, immediately above `_weather_terminal_label_v2()`.

**Why:** `_weather_terminal_label_v2()` had no post-call validation. An unknown label would reach `weather_scout_log` (permanent DB write) and the API response undetected. The CC ceiling resolver (`cc_labels.ceiling_rank`) returns 0 (FINAL_APPROVED-level permissive) for any unknown label, so the gap was also a CC bypass risk.

## Registry — 6 confirmed-reachable labels
```
KALSHI_PLAYABLE_LIMIT_ONLY
KALSHI_WATCH
KALSHI_REJECT_NO_EDGE
KALSHI_REJECT_BAD_RULES
KALSHI_REJECT_UNCALIBRATED
KALSHI_DATA_UNOBTAINABLE
```

Intentionally excluded (docstring-only, no reachable return statement):
- `KALSHI_REJECT_THIN_BOOK`
- `KALSHI_REJECT_FEE_DRAG`

## How to apply
- Guard is applied at **both** `_weather_terminal_label_v2()` call sites in `app.py`:
  - `GET /kalshi/evaluate/weather/<city>` (around original line 23753)
  - `POST /wow/kalshi/weather/evaluate` (around original line 25496)
- Unknown label → HTTP 500 `INTERNAL_LABEL_VIOLATION`, `can_execute=False`, ledger write suppressed, error logged with route + label.
- Valid label → zero behavior change, byte-for-byte identical response.

## Isolation invariant
`_validate_wx_terminal_label` and `_KALSHI_WX_TERMINAL_LABEL_REGISTRY` must NEVER be imported by or called from:
- `gate_engine/wow_runtime_manifest.py`
- `gate_engine/command_center/cc_labels.py`
- `gate_engine/command_center/ceiling_resolver.py`
Tests F1–F3 enforce this with a grep-based assertion.

## Auth pattern for integration tests
The `require_api_key` decorator reads `SCORING_API_KEY` (not `GPT_ACTION_SECRET`).
Tests use `patch.dict(os.environ, {"SCORING_API_KEY": _TEST_API_KEY})` and pass the same sentinel value as `X-API-Key` header.

## Test file
`tests/test_kalshi_wx_terminal_label_failclosed.py` — 29 tests, 6 subtests. Sections:
- A: registry membership (9 tests)
- B: validate function contract (7 tests)
- C: POST route valid labels pass through (6 tests, one per label)
- D: POST route adversarial — invented string + THIN_BOOK (2 tests)
- E: GET route adversarial (2 tests)
- F: ceiling resolver isolation via grep (3 tests)
