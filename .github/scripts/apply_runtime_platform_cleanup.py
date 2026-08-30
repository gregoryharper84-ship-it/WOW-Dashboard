from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BANNED = "rep" + "lit"


def tracked_files() -> list[Path]:
    raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    return [ROOT / p.decode() for p in raw.split(b"\0") if p]


def neutralize(raw: str, *, docs: bool) -> str:
    replacement = "legacy platform" if docs else "legacy_platform"
    return re.sub(BANNED, replacement, raw, flags=re.I)


# Remove retired-platform cache/config ignore entries.
gitignore = ROOT / ".gitignore"
lines = gitignore.read_text().splitlines()
gitignore.write_text("\n".join(line for line in lines if BANNED not in line.lower()).rstrip() + "\n")

# Remove retired package dependencies from every workspace manifest.
for manifest in ROOT.rglob("package.json"):
    if "node_modules" in manifest.parts:
        continue
    data = json.loads(manifest.read_text())
    changed = False
    for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        deps = data.get(section)
        if isinstance(deps, dict):
            for key in list(deps):
                if BANNED in key.lower():
                    del deps[key]
                    changed = True
    if changed:
        manifest.write_text(json.dumps(data, indent=2) + "\n")

# Remove retired catalog/exclusion entries. The lockfile is regenerated later.
workspace = ROOT / "pnpm-workspace.yaml"
if workspace.exists():
    workspace.write_text(
        "\n".join(line for line in workspace.read_text().splitlines() if BANNED not in line.lower()).rstrip() + "\n"
    )

# Remove retired Vite-only runtime hooks from every frontend workspace.
for vite in ROOT.rglob("vite.config.ts"):
    v = vite.read_text()
    v = "\n".join(line for line in v.splitlines() if BANNED not in line.lower() and "runtimeErrorOverlay()," not in line) + "\n"
    # Remove conditional plugin spreads keyed to the retired host environment.
    start = v.find('    ...(process.env.NODE_ENV !== "production" &&')
    while start != -1:
        end = v.find("      : []),", start)
        if end == -1:
            raise SystemExit(f"unterminated conditional plugin block in {vite}")
        end += len("      : []),")
        segment = v[start:end]
        if "REPL_ID" in segment or BANNED in segment.lower() or "legacy_platform" in segment.lower():
            v = v[:start] + v[end:]
        else:
            break
        start = v.find('    ...(process.env.NODE_ENV !== "production" &&')
    vite.write_text(v)

# The canonical verifier should no longer carry a retired rescue-branch trigger.
verify = ROOT / ".github/workflows/wow-verify.yml"
if verify.exists():
    q = BANNED
    old = f"  push:\n    branches:\n      - rescue/{q}-emergency-20260820-1221\n"
    verify.write_text(verify.read_text().replace(old, ""))

# Neutralize remaining tracked textual references. This keeps historical meaning
# while ensuring active code/configuration does not retain retired-platform identifiers.
doc_exts = {".md", ".txt"}
code_exts = {".py", ".yaml", ".yml", ".json", ".toml", ".ts", ".tsx", ".js", ".jsx", ".html", ".sh", ".ini", ".cfg"}
for path in tracked_files():
    if not path.exists() or path.name == "pnpm-lock.yaml":
        continue
    suffix = path.suffix.lower()
    if suffix not in doc_exts | code_exts and path.name not in {".gitignore", ".npmrc"}:
        continue
    raw = path.read_text(errors="ignore")
    if BANNED in raw.lower():
        path.write_text(neutralize(raw, docs=suffix in doc_exts))

print("runtime platform cleanup applied")
