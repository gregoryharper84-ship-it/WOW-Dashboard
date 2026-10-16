"""Conservative provider-egress preflight; defense in depth, NOT certified DLP.

Never auto-redact a detected secret into a still-transmittable payload. A human
or independently approved sanitization service must prepare public/sanitized
evidence before Gemini receives it. No secrets or original snippets are logged.
"""
from __future__ import annotations

import re

CAN_EXECUTE = False
REASON = "GEMINI_SENSITIVE_MATERIAL_REJECTED"
_PATTERNS = (
    re.compile(r"(?i)\b(?:SUPABASE_SERVICE_ROLE_KEY|SUPABASE_SERVICE_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY|GEMINI_API_KEY|GOOGLE_API_KEY|AWS_SECRET_ACCESS_KEY|DB_PASSWORD|DATABASE_URL)\s*[:=]\s*[^\s'\"]+"),
    re.compile(r"(?i)\bAuthorization\s*:\s*Bearer\s+[^\s]+"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\b(?:postgres|postgresql|mysql)://[^\s/@:]+:[^\s/@]+@"),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    # Conservative U.S. PII patterns; not comprehensive DLP certification.
    # SSN-like formatting, including the exact synthetic forms in QA findings.
    re.compile(r"(?<!\d)\d{3}[- ]\d{2}[- ]\d{4}(?!\d)"),
    re.compile(r"(?i)\b(?:ssn|social security(?: number)?)\s*[:#-]?\s*\d{3}[- ]?\d{2}[- ]?\d{4}\b"),
    # Fail closed for bare nine-digit tokens: may also reject non-SSN identifiers.
    # Explicit digit adjacency, not \b: underscore/letters are word characters.
    # Reject any isolated nine-digit run including record_123456789 and 123456789_record.
    re.compile(r"(?<![0-9])[0-9]{9}(?![0-9])"),
    # U.S. ten-digit phone numbers, with optional country code and delimiters.
    re.compile(r"(?<![A-Za-z0-9])(?:\+?1[\s.()-]*)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)"),

)


class GeminiSensitiveMaterialError(Exception):
    def __init__(self):
        super().__init__(REASON)
        self.code = REASON


def ensure_no_obvious_sensitive_material(*sections: str) -> None:
    """Reject clearly sensitive patterns without returning matching input."""
    for section in sections:
        if not isinstance(section, str):
            raise GeminiSensitiveMaterialError()
        if any(pattern.search(section) for pattern in _PATTERNS):
            raise GeminiSensitiveMaterialError()
