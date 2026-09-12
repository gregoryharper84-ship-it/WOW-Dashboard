from __future__ import annotations

import argparse
import json
from pathlib import Path

from v17.scout_brain_persistence import persist_handoff


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    handoff = json.loads(Path(args.input).read_text(encoding="utf-8"))
    receipt = persist_handoff(handoff)
    Path(args.receipt).write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
