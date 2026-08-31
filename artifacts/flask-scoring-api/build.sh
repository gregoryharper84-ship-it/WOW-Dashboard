#!/bin/bash
set -euo pipefail

# Runtime-neutral paths: resolve from this script and the checked-out Git tree.
# Do not assume legacy_platform, GitHub Actions, or any provider-specific workspace root.
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "$APP_DIR" rev-parse --show-toplevel)"
export APP_DIR REPO_ROOT

# Capture immutable source provenance while the build workspace still has Git
# metadata. Production does not rely on .git being packaged into the artifact.
python - <<'PY'
import datetime
import json
import os
import pathlib
import re
import subprocess

root = pathlib.Path(os.environ["REPO_ROOT"])
app_dir = pathlib.Path(os.environ["APP_DIR"])
out = app_dir / "runtime_build_info.json"

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
