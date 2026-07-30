import os
import sys

REQUIRED_ENV_VARS = [
    "DATABASE_URL",
    "SCORING_API_KEY",
    "ODDS_API_KEY",
]

missing = [var for var in REQUIRED_ENV_VARS if not os.environ.get(var)]

if missing:
    for var in missing:
        print(f"FATAL: missing env var: {var}", file=sys.stderr)
    sys.exit(1)

print("pre_start: all required env vars present, starting gunicorn", flush=True)

# ---------------------------------------------------------------------------
# Skill file validation (Section 8 — startup integrity check)
# ---------------------------------------------------------------------------
try:
    _here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, _here)
    from gate_engine.wow_runtime_manifest import validate_skill_files
    _skill_check = validate_skill_files(os.path.join(_here, "skills"))
    if _skill_check["missing"]:
        print(
            f"pre_start: WARN: required skill files missing: {_skill_check['missing']} — "
            "engine will start DEGRADED (skill validation will fail in /wow/engine/health)",
            flush=True,
        )
    else:
        print(
            f"pre_start: skill file validation OK "
            f"({len(_skill_check['present'])} files present)",
            flush=True,
        )
except Exception as _e:
    print(f"pre_start: skill file validation skipped ({_e})", flush=True)
