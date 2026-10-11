"""PUBLIC SYNTHETIC regression fixture — deliberately vulnerable pseudo gateway.
NOT the real WOW NFL gateway. Not a probability model. No credentials or PII.
Only send this reviewed fixed fixture to Gemini under separately approved policy.
"""

GATEWAY_FIXTURE_CODE = '''
# VULNERABILITY 1: repeated rows accepted across pages without event identity key.
def store_batch_pages(pages, upsert):
    for page in pages:
        for record in page:
            upsert(record)  # synthetic: lacks unique (event_id, market, requested_at)

# VULNERABILITY 2: authorization presence is not bearer-token/JWT verification.
def is_authorized(headers):
    return bool(headers.get("Authorization"))

# VULNERABILITY 3: a market-quote display path rounds floating numbers too early.
# Sportsbook quote formatting is NOT governed sporting model probability.
def normalize_quote_for_display(decimal_quote):
    return round(float(decimal_quote), 2)
'''

TASK = (
    "Review this PUBLIC SYNTHETIC gateway fixture. It has exactly three intentionally "
    "planted faults: non-idempotent duplicate rows, incomplete bearer/JWT validation, "
    "and premature float rounding of a market quote DISPLAY value. "
    "Return JSON: summary string, findings array of strings, evidence_refs array "
    "of strings. For each issue, propose a reproducible negative pytest assertion. "
    "Do not infer any NFL win/cover/player probability from market quotes, "
    "do not claim you ran the tests, and do not certify production systems."
)
