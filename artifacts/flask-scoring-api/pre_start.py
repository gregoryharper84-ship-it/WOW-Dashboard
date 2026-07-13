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
