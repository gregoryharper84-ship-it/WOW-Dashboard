"""Explicit offline-only challenge runner, never imported by production."""
import argparse
import os
import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--impl", choices=("starter", "reference"), default="starter")
    args = p.parse_args()
    env = dict(os.environ, WOW_CHALLENGE_IMPL=args.impl)
    root = Path(__file__).resolve().parent
    raise SystemExit(subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(root), "-p", "test_contracts.py", "-v"],
        env=env, cwd=root, check=False,
    ).returncode)
