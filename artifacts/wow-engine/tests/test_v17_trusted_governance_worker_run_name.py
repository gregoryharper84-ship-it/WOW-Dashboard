"""Execute the protected run-name normalization from both source workflows.

Provenance must remain bounded: only an exact worker prefix and allowed
lease/incident grammar is normalized. The pre-existing path/event/ref/repository
checks remain necessary and must never be relaxed.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[3]
GATES = (
    ROOT / ".github/workflows/wow-v17-trusted-governance-gate.yml",
    ROOT / ".github/workflows/wow-v17-release-production-verification-agent.yml",
)


def normalize_from_real_workflow(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    assert 'case "$canonical_name|$path" in' in source
    assert 'canonical_name="$name"' in source
    fragment = source.split('canonical_name="$name"', 1)[1].split(
        'case "$canonical_name|$path" in', 1
    )[0]
    command = (
        'set -euo pipefail\n'
        'name="$1"\n'
        'canonical_name="$name"\n'
        + fragment
        + '\nprintf "%s\\n" "$canonical_name"\n'
    )
    result = subprocess.run(
        ["bash", "-c", command, "bash", name],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    return result.stdout.removesuffix("\n")


class TrustedRunNameRegression(unittest.TestCase):
    def test_approved_bounded_variants_and_rejected_suffixes(self):
        for gate in GATES:
            self.assertTrue(gate.is_file(), str(gate))
            for kind in ("chatgpt", "claude"):
                bare = f"wow-v17-{kind}-engineering-worker"
                allowed = (
                    bare,
                    f"{bare} lease=GLOBAL incident=AUTO",
                    f"{bare} lease=NFL_ML incident=823",
                )
                disallowed = (
                    f"{bare} lease=global incident=AUTO",
                    f"{bare} lease=GLOBAL incident=abc",
                    f"{bare} lease=GLOBAL incident=823 extra",
                    f"{bare} lease=GLOBAL incident=12345678901",
                    f"{bare} lease={'Z'*49} incident=823",
                    f"{bare} lease=GLOBAL;echo-unsafe incident=823",
                    f"{bare} lease=GLOBAL incident=823\nuntrusted",
                    f"{bare}\nlease=GLOBAL incident=AUTO",
                    f"{bare} lease=GLOBAL\nincident=AUTO",
                    f"{bare}\tlease=GLOBAL incident=AUTO",
                    f"{bare} lease=GLOBAL\tincident=AUTO",
                    f"{bare}  lease=GLOBAL incident=AUTO",
                    f"{bare} lease=GLOBAL  incident=AUTO",
                    f"{bare} lease=GLOBAL\rincident=AUTO",
                    f"{bare}\rlease=GLOBAL incident=AUTO",
                    f"evil-{bare} lease=GLOBAL incident=823",
                )
                for value in allowed:
                    self.assertEqual(
                        normalize_from_real_workflow(gate, value), bare,
                        (gate, value),
                    )
                for value in disallowed:
                    self.assertEqual(
                        normalize_from_real_workflow(gate, value), value,
                        (gate, value),
                    )

    def test_exact_path_binding_and_canonical_receipt_remain(self):
        for gate in GATES:
            source = gate.read_text(encoding="utf-8")
            for kind in ("chatgpt", "claude"):
                self.assertIn(
                    f'"wow-v17-{kind}-engineering-worker|.github/workflows/'
                    f'wow-v17-{kind}-engineering-worker.yml"',
                    source,
                )
            self.assertIn('echo "name=$canonical_name" >> "$GITHUB_OUTPUT"', source)
            self.assertIn('head_repo', source)
            self.assertIn('head_branch', source)
            self.assertIn('conclusion', source)


if __name__ == "__main__":
    unittest.main()
