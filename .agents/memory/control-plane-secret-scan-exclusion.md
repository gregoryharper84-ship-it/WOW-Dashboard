---
name: Control-plane secret scan exclusion policy
description: Narrow per-file exclusions for wow-preflight secret scan; HTTP 000 with live check enabled is a hard fail in wow-verify-patch.
---

## Secret scan exclusion rules (wow-preflight)

**Never** use blanket `--exclude-dir=tests` or `--exclude="test_*.py"` for the `api_key` or `password` patterns. Real secrets could be hidden in test files.

Instead use a narrow per-file allowlist (`BROAD_PATTERN_FIXTURE_EXCLUDE_ARGS`) listing only files whose synthetic fixture content is documented in the script:

```bash
BROAD_PATTERN_FIXTURE_EXCLUDE_ARGS=(
    --exclude="test_governance_api.py"                       # _TEST_API_KEY = "test-key-governance"
    --exclude="test_kalshi_wx_terminal_label_failclosed.py"  # _TEST_API_KEY = "test-scoring-key-failclosed-patch"
    --exclude="test_kalshi_wx_active_status_normalization.py" # _TEST_API_KEY = "test-key-wx-active-status-norm"
    --exclude="test_control_plane_scripts.py"                 # scanner test vectors
)
```

For `sk-/Bearer` patterns, directory-level `TEST_INFRA_EXCLUDE_ARGS` is acceptable because `test_control_plane_scripts.py` writes fake `sk-` keys to temp files specifically to test the scanner.

**Adding a new synthetic-fixture test file:** Add it to `BROAD_PATTERN_FIXTURE_EXCLUDE_ARGS` with a comment naming the fixture constant and its value. Do NOT add it to `TEST_INFRA_EXCLUDE_ARGS` unless it is scanner infrastructure.

## HTTP 000 in wow-verify-patch

When `GPT_ACTION_SECRET` is set (live check enabled), HTTP 000 (connection refused) is a **hard failure** — not a warning. Any non-200 response fails the check. The rationale: if the engineer explicitly requested live verification, "unreachable" is itself an error.

- `test_live_check_http_000_fails_when_enabled` in `test_control_plane_scripts.py` locks this behavior.
- Uses `REPLIT_APP_URL=http://localhost:1` + `GPT_ACTION_SECRET=fake` to guarantee connection refused.
- When `GPT_ACTION_SECRET` is absent (check skipped), exit code is not affected.

## Why

External verifier (finding 3): blanket test exclusion allows real secrets placed in tests to bypass detection. Finding 4: HTTP 000 cannot become a warning when live verification is explicitly requested.
