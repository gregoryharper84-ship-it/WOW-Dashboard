---
name: Category handler lambda pattern
description: Why _CATEGORY_HANDLERS must use lambdas instead of direct function references for test monkey-patching to work.
---

## Rule
`_CATEGORY_HANDLERS` dicts that map category names to handler functions must use lambda wrappers (`lambda p, e: _fn(p, e)`) rather than direct function references (`_fn`).

## Why
A direct reference (`"role_status": _attempt_role_status`) captures the function object at module load time. When a test does `_fr._attempt_role_status = fake_fn`, it replaces the module attribute but the dict still holds the original function object — the patch has no effect. Lambdas look up the current module-level name at call time, so monkey-patches are picked up correctly.

## How to apply
Any dict of the form `{key: handler_fn}` that is expected to be patchable in tests must use lambdas:
```python
_CATEGORY_HANDLERS = {
    "role_status": lambda p, e: _attempt_role_status(p, e),
    ...
}
```
This applies to `gate_engine/wnba/fallback_router.py` and any similar dispatch tables elsewhere.
