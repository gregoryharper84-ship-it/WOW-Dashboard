---
name: Frozen dataclass test pattern
description: object.__setattr__ bypasses frozen dataclass protection; tests verifying immutability must use direct assignment.
---

# Frozen dataclass test pattern

## The rule
To verify that a `@dataclass(frozen=True)` instance raises when you try to mutate it, use **direct attribute assignment** — NOT `object.__setattr__`.

```python
# CORRECT: raises FrozenInstanceError (subclass of AttributeError)
with self.assertRaises((AttributeError, TypeError)):
    result.enforcement_code = "MUTATED"

# WRONG: object.__setattr__ BYPASSES the frozen guard — it silently succeeds
# This is used in test fixtures to SET up frozen objects, not to VERIFY the guard
with self.assertRaises((AttributeError, TypeError)):
    object.__setattr__(result, "enforcement_code", "MUTATED")  # doesn't raise!
```

**Why:** `object.__setattr__` is the mechanism used to construct frozen dataclasses in the first place (CPython's `dataclasses` module uses it internally). Calling it from outside the class also bypasses the `__setattr__` override that enforces immutability.

**How to apply:** Any test that wants to verify immutability of a frozen dataclass must use plain `result.field = value`. Tests that need to construct or mutate a frozen object for testing purposes (e.g., to simulate a specific state) use `object.__setattr__`.
