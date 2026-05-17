"""
Final output guardrail: redact any PII that should never appear in agent messages.
This is a defense-in-depth layer — templated responses shouldn't contain PII anyway,
but this catches any LLM-generated content or edge cases.
"""
from __future__ import annotations

import re
from core.state_machine import AccountRecord

REDACTED = "[REDACTED]"


def _redact_pattern(text: str, pattern: str) -> str:
    return re.sub(pattern, REDACTED, text, flags=re.IGNORECASE)


def redact_pii(message: str, account: AccountRecord | None) -> str:
    if account is None:
        return message

    result = message

    # Redact DOB (various formats)
    dob = account.dob
    dob_patterns = [
        re.escape(dob.strftime("%Y-%m-%d")),         # 1990-05-14
        re.escape(dob.strftime("%d-%m-%Y")),          # 14-05-1990
        re.escape(dob.strftime("%d/%m/%Y")),          # 14/05/1990
        re.escape(dob.strftime("%m/%d/%Y")),          # 05/14/1990
        re.escape(dob.strftime("%-d %B %Y")),         # 14 May 1990
        re.escape(dob.strftime("%B %-d, %Y")),        # May 14, 1990
        re.escape(str(dob.year)),                     # just the year alone is OK to skip
    ]
    for pat in dob_patterns[:-1]:  # skip year-only — too broad
        result = _redact_pattern(result, pat)

    # Redact Aadhaar last 4 (only if standalone 4-digit sequence matching)
    aadhaar = account.aadhaar_last4
    result = re.sub(
        rf"(?<!\d){re.escape(aadhaar)}(?!\d)",
        REDACTED,
        result,
    )

    # Redact pincode
    pincode = account.pincode
    result = re.sub(
        rf"(?<!\d){re.escape(pincode)}(?!\d)",
        REDACTED,
        result,
    )

    return result


def contains_pii(message: str, account: AccountRecord | None) -> bool:
    """Used in eval to detect PII leaks."""
    if account is None:
        return False
    dob = account.dob
    checks = [
        dob.strftime("%Y-%m-%d"),
        dob.strftime("%d-%m-%Y"),
        dob.strftime("%-d %B %Y"),
        account.aadhaar_last4,
        account.pincode,
    ]
    msg_lower = message.lower()
    return any(c.lower() in msg_lower for c in checks)
