---
name: Control-plane secret scan exclusion policy (R2)
description: Line-level fixture exclusions for wow-preflight + scanner-tooling allowlist for wow-verify-patch — R2 design replacing blanket test-dir / file-level exclusions
---

## Rule

wow-preflight applies a `_filter_fixture_literals()` post-filter to the raw secret-scan grep output.
wow-verify-patch applies a `_is_scanner_tooling_file()` allowlist before scanning changed files.

**Why:** file-level exclusions (`--exclude=test_governance_api.py`) excluded entire files regardless of value;
a real secret could hide behind the exclusion. R2 narrows to file-path+fixture-value pairs.

**How to apply:**
- Any new test file that uses `_TEST_API_KEY = "..."` or similar must have its exact fixture value
  added to BOTH `_filter_fixture_literals` in `scripts/wow-preflight` AND documented in the comment block.
- `scripts/wow-preflight` and `gate_engine/tests/test_control_plane_scripts.py` are the only files
  in `_SCANNER_TOOLING_FILES` in `scripts/wow-verify-patch`. If a third scanner-tooling file is added,
  add it to that list too.
- `.agents/` is in `SECRET_EXCLUDE_ARGS` (not source code; cannot contain real secrets by convention).
- When writing proof tests that verify the scanner catches realistic secrets: construct the realistic
  secret VALUE via programmatic concatenation (e.g. `"sk-" + "A" * 30`) so no verbatim pattern appears
  as a string literal in the test source file — the scanner will catch it in comments too.

## Documented fixture locations (7 file+value pairs, as of R2)

| file | fixture value |
|---|---|
| `test_governance_api.py` | `test-key-governance` |
| `test_kalshi_wx_terminal_label_failclosed.py` | `test-scoring-key-failclosed-patch` |
| `test_kalshi_wx_active_status_normalization.py` | `test-key-wx-active-status-norm` |
| `test_control_plane_scripts.py` | `sk-thisisaverylongsecretkey` |
| `test_control_plane_scripts.py` | `sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456` |
| `test_control_plane_scripts.py` | `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abcdefghij` (prefix — covers .abcdefghijklmnop too) |
| `test_control_plane_scripts.py` | `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xxxxxx` |

## Scanner-tooling allowlist (wow-verify-patch)

| file | reason |
|---|---|
| `scripts/wow-preflight` | Contains fixture values as exclusion patterns — MUST have them |
| `gate_engine/tests/test_control_plane_scripts.py` | Contains scanner test vectors — MUST have them |
