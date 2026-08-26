#!/bin/bash
set -euo pipefail

ROOT="/home/runner/workspace"
APP_DIR="$ROOT/artifacts/flask-scoring-api"

# Capture immutable source provenance while the build workspace still has Git
# metadata. Production does not rely on .git being packaged into the artifact.
python - <<'PY'
import datetime
import json
import pathlib
import re
import subprocess

root = pathlib.Path("/home/runner/workspace")
out = root / "artifacts/flask-scoring-api/runtime_build_info.json"

def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, timeout=10
    ).strip()

sha = git("rev-parse", "HEAD").lower()
ref = git("branch", "--show-current") or "DETACHED"
dirty = bool(git("status", "--porcelain"))
valid_sha = bool(re.fullmatch(r"[0-9a-f]{40}", sha))
attested = bool(valid_sha and ref and not dirty)

payload = {
    "attestation_version": 1,
    "source_sha": sha if valid_sha else None,
    "source_ref": ref or None,
    "worktree_clean": not dirty,
    "build_attested": attested,
    "build_generated_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
}
out.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
if not attested:
    raise SystemExit("BUILD_ATTESTATION_FAILED: source SHA/ref unavailable or worktree dirty")
print(f"BUILD_ATTESTATION source_sha={sha} source_ref={ref} clean={not dirty}")
PY

pip install -r "$APP_DIR/requirements.txt"
