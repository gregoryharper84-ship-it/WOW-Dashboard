"""Self-contained pilot tests for parsing a WOW-Copilot-Receipt line.

The parser is defined inside this module on purpose; it is not a production
helper. No network, API, secret, or filesystem writes are used.
"""

import re
import unittest

_RECEIPT_RE = re.compile(
    r"\AWOW-Copilot-Receipt: incident=(?P<incident>[0-9]+) sha=(?P<sha>[0-9a-f]{40})\Z"
)


def parse_receipt(line, expected_incident=None):
    """Return (incident, sha) for a valid single-line receipt, else None."""
    if not isinstance(line, str):
        return None
    match = _RECEIPT_RE.match(line)
    if match is None:
        return None
    incident = int(match.group("incident"))
    if expected_incident is not None and incident != expected_incident:
        return None
    return incident, match.group("sha")


class CopilotReceiptParserTest(unittest.TestCase):
    SHA = "0123456789abcdef0123456789abcdef01234567"

    def test_valid_receipt(self):
        line = f"WOW-Copilot-Receipt: incident=1763 sha={self.SHA}"
        self.assertEqual(parse_receipt(line, 1763), (1763, self.SHA))

    def test_wrong_incident(self):
        line = f"WOW-Copilot-Receipt: incident=1764 sha={self.SHA}"
        self.assertIsNone(parse_receipt(line, 1763))

    def test_uppercase_hash_rejected(self):
        line = f"WOW-Copilot-Receipt: incident=1763 sha={self.SHA.upper()}"
        self.assertIsNone(parse_receipt(line, 1763))

    def test_missing_sha(self):
        self.assertIsNone(parse_receipt("WOW-Copilot-Receipt: incident=1763", 1763))
        self.assertIsNone(parse_receipt("WOW-Copilot-Receipt: incident=1763 sha=", 1763))

    def test_multiline_receipt_rejected(self):
        base = f"WOW-Copilot-Receipt: incident=1763 sha={self.SHA}"
        self.assertIsNone(parse_receipt(base + "\n", 1763))
        self.assertIsNone(parse_receipt(base + "\nextra", 1763))
        self.assertIsNone(parse_receipt("prefix\n" + base, 1763))


if __name__ == "__main__":
    unittest.main()
