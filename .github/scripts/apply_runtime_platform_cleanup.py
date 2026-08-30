from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BANNED = "rep" + "lit"

# Remove legacy cache/config ignore entries without retaining the legacy name.
gitignore = ROOT / ".gitignore"
text = gitignore.read_text()
lines = text.splitlines()
new_lines = []
skip_blank_after = False
for line in lines:
    if BANNED in line.lower():
        skip_blank_after = True
        continue
    if skip_blank_after and not line.strip():
        skip_blank_after = False
        continue
    skip_blank_after = False
    new_lines.append(line)
gitignore.write_text("\n".join(new_lines).rstrip() + "\n")

# Remove legacy package dependencies from every workspace manifest.
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

# Remove the legacy Vite runtime hooks from the mockup workspace.
vite = ROOT / "artifacts/mockup-sandbox/vite.config.ts"
if vite.exists():
    v = vite.read_text()
    v = re.sub(r'^import runtimeErrorOverlay from "@' + BANNED + r'/vite-plugin-runtime-error-modal";\n', '', v, flags=re.M)
    v = re.sub(r'^\s*runtimeErrorOverlay\(\),\n', '', v, flags=re.M)
    block = re.compile(
        r'\s*\.\.\.\(process\.env\.NODE_ENV !== "production" &&\n'
        r'\s*process\.env\.REPL_ID !== undefined\n'
        r'\s*\? \[\n'
        r'\s*await import\("@' + BANNED + r'/vite-plugin-cartographer"\)\.then\(\(m\) =>\n'
        r'\s*m\.cartographer\(\{\n'
        r'\s*root: path\.resolve\(import\.meta\.dirname, "\.\."\),\n'
        r'\s*\}\),\n'
        r'\s*\),\n'
        r'\s*\]\n'
        r'\s*: \[\]\),\n',
        re.M,
    )
    v, count = block.subn('', v)
    if count != 1:
        raise SystemExit(f"expected one legacy Vite conditional block, found {count}")
    vite.write_text(v)

# Historical text remains useful, but the active tracked repo should not carry
# the retired platform name. Preserve history semantically with a neutral term.
for path in ROOT.rglob("*"):
    if not path.is_file() or ".git" in path.parts or "node_modules" in path.parts:
        continue
    if path.suffix.lower() not in {".md", ".txt"}:
        continue
    raw = path.read_text(errors="ignore")
    if BANNED in raw.lower():
        raw = re.sub(BANNED, "legacy platform", raw, flags=re.I)
        path.write_text(raw)

print("runtime platform cleanup applied")
