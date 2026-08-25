---
name: B3C Canary Build Lessons
description: Three non-obvious bugs hit during B3C bounded-Claude canary build; patterns to avoid.
---

## Lesson 1 — EvidencePacket has no `scoring_context`
**Rule:** Use `packet.to_dict()` or named fields (`market_snapshot`, `deterministic_model_inputs`, `event_name`, etc.) when building prompts from a real `EvidencePacket`. The field `scoring_context` does NOT exist.

**Why:** Mock packets in unit tests can have arbitrary attributes; real `EvidencePacket` (frozen dataclass) only has the fields defined in `evidence_packet.py`. A `hasattr(packet, 'to_dict')` guard lets the same function work with both.

**How to apply:** Any module that builds a prompt or context dict from an `EvidencePacket` must use `packet.to_dict()` or named attribute access — never a synthetic attribute name. Wrap prompt-building in try/except so unhandled errors still reach `call_log.append()`.

---

## Lesson 2 — `patch.object(Class, 'method', wraps=...)` needs `autospec=True` for instance-method call_count

**Rule:** When asserting that an instance method was called N times using `patch.object(SomeClass, 'method_name', wraps=SomeClass.method_name)`, ALWAYS add `autospec=True`.

**Why:** Without `autospec=True`, `patch.object` replaces the class attribute with a plain MagicMock that is NOT a descriptor. Python's attribute lookup for `instance.method_name` returns the MagicMock from the class, but without descriptor binding, some calls may create NEW bound mock objects whose calls are not tracked on the original mock. Result: `mock.call_count` stays at 1 regardless of how many instances called the method.  
With `autospec=True`, the mock is spec'd as a proper descriptor and all instance calls accumulate on the same mock.

**How to apply:**
```python
with patch.object(SomeClass, 'some_method', autospec=True,
                  wraps=SomeClass.some_method) as mock_method:
    ... run_pipeline() ...
self.assertGreater(mock_method.call_count, 0)
```

---

## Lesson 3 — Model-string source-scan tests need updating when a new module pins the same model

**Rule:** When a new module introduces a literal pinned model string (e.g., `"claude-haiku-4-5-20251001"`), update any source-scan test (M6 pattern in `test_kalshi_wx_shadow_model_migration.py`) that whitelists which files may contain that string.

**Why:** The M6 test scans all non-test `.py` files under `gate_engine/` and fails if the model string appears anywhere outside an authorized file list. Adding B3C (`canary_config.py`, `claude_role_runner.py`, `canary/__init__.py`) to the authorized list restores the scan.

**How to apply:** Search for `_NEW_MODEL` or model-string whitelist in `tests/test_kalshi_wx_shadow_model_migration.py` and add the new file names to `_AUTHORIZED`.

---

## Lesson 4 — `object.__setattr__()` bypasses frozen dataclass guard

**Rule:** To test that a frozen dataclass is frozen, use REGULAR assignment (`result.field = value`), NOT `object.__setattr__(result, 'field', value)`.

**Why:** `object.__setattr__` bypasses the dataclass's custom `__setattr__`, which is how `frozen=True` is enforced. Regular assignment goes through `__setattr__`, which raises `FrozenInstanceError(AttributeError)`. (Same lesson documented in kalshi-wx-shadow-snapshot.md, restated here for B3C context.)
