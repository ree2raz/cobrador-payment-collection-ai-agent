"""
Final output guardrail: redact any PII that should never appear in agent messages.
This is a defense-in-depth layer — templated responses shouldn't contain PII anyway,
but this catches any LLM-generated content or edge cases.
"""
from __future__ import annotations

from datetime import date
import re
from core.state_machine import AccountRecord

REDACTED = "[REDACTED]"


def _redact_pattern(text: str, pattern: str) -> str:
    return re.sub(pattern, REDACTED, text, flags=re.IGNORECASE)


def _dob_patterns(dob: date) -> list[str]:
    """DOB renderings commonly produced by users/LLMs.

    Keep these specific to the full date. Redacting year-only values creates
    too many false positives in normal payment conversations.
    """
    day = dob.day
    month_name = dob.strftime("%B")
    month_short = dob.strftime("%b")
    year = dob.year
    yy = str(year)[-2:]
    dd = f"{day:02d}"
    mm = f"{dob.month:02d}"
    suffix = "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")

    patterns = [
        rf"\b{year}-{mm}-{dd}\b",
        rf"\b{dd}[-/\.]{mm}[-/\.]{year}\b",
        rf"\b{mm}[-/\.]{dd}[-/\.]{year}\b",
        rf"\b{dd}[-/\.]{mm}[-/\.]{yy}\b",
        rf"\b{mm}[-/\.]{dd}[-/\.]{yy}\b",
    ]
    # Textual dates: 14 May 1990, 14th May, 1990, May 14th 1990, etc.
    month_alt = rf"(?:{re.escape(month_name)}|{re.escape(month_short)}\.?)"
    patterns.extend(
        [
            rf"\b{day}(?:{suffix})?\s+{month_alt},?\s+{year}\b",
            rf"\b{day}(?:{suffix})?\s+{month_alt},?\s+{yy}\b",
            rf"\b{month_alt}\s+{day}(?:{suffix})?,?\s+{year}\b",
            rf"\b{month_alt}\s+{day}(?:{suffix})?,?\s+{yy}\b",
        ]
    )
    return patterns


def redact_pii(message: str, account: AccountRecord | None) -> str:
    if account is None:
        return message

    result = message

    # Redact DOB (various formats)
    for pat in _dob_patterns(account.dob):
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
    for pat in _dob_patterns(account.dob):
        if re.search(pat, message, flags=re.IGNORECASE):
            return True
    checks = [account.aadhaar_last4, account.pincode]
    return any(
        re.search(rf"(?<!\d){re.escape(c)}(?!\d)", message)
        for c in checks
    )
