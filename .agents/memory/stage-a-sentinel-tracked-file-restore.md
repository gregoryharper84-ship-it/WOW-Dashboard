---
name: Stage-A sentinel tracked-file restore
description: test_conveyor_summary_logged wrote uac_b9_readiness_ruling.json with a new timestamp every run without restoring it, causing the git-clean-tree sentinel to fail in every full suite run.
---

## Rule

Any test that writes to a **tracked** file must restore it to exact git-HEAD state after the test.  
Use `git checkout HEAD -- <workspace-relative-path>` (via `subprocess.run` in `addCleanup`), NOT `write_text(original_on_disk_content)`.

**Why:** `write_text(original)` restores only the content that was on disk *before this test run*. If the file was already different from HEAD (from a previous run that didn't clean up), you restore the wrong content. `git checkout HEAD` is idempotent and always correct.

**How to apply:**
```python
def _restore_to_head():
    subprocess.run(
        ["git", "checkout", "HEAD", "--", "artifacts/flask-scoring-api/uac_b9_readiness_ruling.json"],
        cwd=str(_REPO_ROOT.parent.parent),  # workspace root
        capture_output=True,
    )
self.addCleanup(_restore_to_head)
```

Apply this pattern to every test that writes a tracked file, especially any "evidence log" test that stamps a new timestamp each run.

## Root cause found in

`gate_engine/tests/test_b9_readiness_ruling.py::TestR13ConveyorSummary::test_conveyor_summary_logged`  
— writes `uac_b9_readiness_ruling.json` with `datetime.utcnow()` timestamp, leaving the file modified.  
Fixed with the `git checkout HEAD` addCleanup pattern above.
