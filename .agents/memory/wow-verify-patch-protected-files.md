---
name: wow-verify-patch protected files
description: Four files in gate_engine/ are hard-protected by wow-verify-patch — any patch diff touching them fails check 1 unconditionally.
---

## Rule

These four files must NEVER appear in a patch commit's diff:
- `artifacts/flask-scoring-api/gate_engine/labels.py`
- `artifacts/flask-scoring-api/gate_engine/llp_governance.py`
- `artifacts/flask-scoring-api/gate_engine/data_contract.py`
- `artifacts/flask-scoring-api/gate_engine/failure_path.py`

**Why:** `wow-verify-patch` runs `git diff <commit>^..<commit>` and fails check 1 if any of these appear. The check is unconditional — no override, no allowlist.

**How to apply:**
- New terminal label strings for a patch: define as module-level string constants in the originating module (e.g., `LABEL_X = "RUN_INVALID — X"`), NOT as new PropLabel members in labels.py.
- If two commits together produce a net-zero change to a protected file, squash them (`git reset --soft HEAD~2`) so the file disappears from the combined diff — then recommit.
- Verify before committing: `git diff --cached --name-only | grep labels.py` should return nothing.
