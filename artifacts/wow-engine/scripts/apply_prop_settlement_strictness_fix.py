from pathlib import Path

path = Path(__file__).resolve().parents[1] / "api_prod_market.py"
text = path.read_text()
replacements = [
    ("    settlement_rule: Optional[SettlementRuleInput] = None\n    line_tolerance: float = 0.0\n", "    settlement_rule: Optional[SettlementRuleInput] = None\n"),
    ("        line_tolerance=req.line_tolerance,\n", "        # Exact line matching is server-owned. No caller may widen tolerance.\n        line_tolerance=0.0,\n"),
]
for old, new in replacements:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one strictness anchor, found {count}: {old!r}")
    text = text.replace(old, new, 1)
path.write_text(text)
print("exact-line strictness fix applied")
