"""
Deterministic regex pre-extractor for identity fields.

This is a belt-and-suspenders layer for `_opportunistic_identity` in agent.py.
Reasoning models (gpt-5.4) sometimes skip identity extraction on dense first-turn
messages like "Hi, my account is ACC1001, name Nithin Jain, DOB 14th May 1990,
I want to pay 400 rupees" because their chain-of-thought picks a single
"primary intent" and treats everything else as incidental. Few-shot examples
in the LLM prompt don't reliably override that.

The regex layer catches the most common explicit patterns deterministically:
  - "name: Nithin Jain" / "name Nithin Jain" / "my name is Nithin Jain"
  - "DOB 14th May 1990" / "DOB: 1990-05-14" / "date of birth 14/05/1990"
  - "aadhaar last 4 is 9876" / "aadhaar ending 9876"
  - "pincode 400001"

It is intentionally conservative — only matches when a labeled keyword is
present. Messy/Hinglish/freeform messages still flow to the LLM. The two
layers compose: regex catches the easy cases, LLM catches the rest.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

from dateutil import parser as dateparser


@dataclass
class IdentityHints:
    full_name: Optional[str] = None
    dob: Optional[date] = None
    dob_ambiguous: bool = False
    aadhaar_last4: Optional[str] = None
    pincode: Optional[str] = None

    def any_captured(self) -> bool:
        return any(
            v is not None and v != ""
            for v in (self.full_name, self.dob, self.aadhaar_last4, self.pincode)
        ) or self.dob_ambiguous


# Name: "name <First Last>" / "my name is <Name>" / "name: Nithin Jain"
# Captures a sequence of 2+ Title-cased tokens after the keyword. Stops at
# punctuation or lowercase words. Strips trailing comma/period.
_NAME_RE = re.compile(
    r"\b(?:my\s+name\s+is|name(?:'s|\s+is)?|i\s+am|i'?m|this\s+is)\s*[:\-]?\s*"
    r"((?:[A-Z][a-zA-Z'\-]+)(?:\s+[A-Z][a-zA-Z'\-]+){1,4})",
    re.IGNORECASE,
)

# DOB keyword followed by anything date-like (we let dateutil parse it).
_DOB_RE = re.compile(
    r"\b(?:DOB|date\s+of\s+birth|d\.?o\.?b\.?|born(?:\s+on)?)\s*[:\-]?\s*"
    r"([0-9A-Za-z\s,/\-\.]{6,40}?)"
    r"(?=,|\.|$|\bI\s|\bmy\s|\bAadhaar\b|\bpincode\b|\bcard\b|\bpay\b)",
    re.IGNORECASE,
)

# Aadhaar last-4 — labeled with "aadhaar last 4 is XXXX" / "aadhaar ending XXXX"
# / "aadhaar XXXX" (4 digits). We deliberately do NOT match 12-digit
# Aadhaar here — the LLM has stricter handling for that to avoid storing
# the full number.
_AADHAAR_RE = re.compile(
    r"\baadhaar(?:\s+(?:last\s+(?:4|four)|ending|ends(?:\s+with)?))?"
    r"\s*[:\-]?\s*(?:is\s+)?(\d{4})\b",
    re.IGNORECASE,
)

# Pincode: 6 digits after "pincode" keyword.
_PINCODE_RE = re.compile(
    r"\bpincode\s*[:\-]?\s*(?:is\s+)?(\d{6})\b",
    re.IGNORECASE,
)

# Ambiguous: pure DD-MM-YYYY / MM-DD-YYYY format where both interpretations
# are plausible (day and month both ≤ 12). dateutil would silently pick one,
# so we flag and let the agent ask.
_AMBIGUOUS_DATE_RE = re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})\b")


def _parse_date(raw: str) -> tuple[Optional[date], bool]:
    """Parse a date string. Returns (date, is_ambiguous). Ambiguous if both
    DD-MM-YY and MM-DD-YY would yield valid dates with different results."""
    raw = raw.strip().rstrip(",.")
    if not raw:
        return None, False

    m = _AMBIGUOUS_DATE_RE.fullmatch(raw)
    if m:
        a, b, _ = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a != b and 1 <= a <= 12 and 1 <= b <= 12:
            return None, True

    # Prefer day-first parsing (Indian convention "14th May 1990" / "14/05/1990").
    try:
        parsed = dateparser.parse(raw, dayfirst=True, fuzzy=True)
    except (ValueError, OverflowError):
        return None, False
    if parsed is None:
        return None, False
    if parsed.year < 1900 or parsed.year > date.today().year:
        return None, False
    return parsed.date(), False


def extract_identity_hints(text: str) -> IdentityHints:
    """Best-effort deterministic extraction. Only matches labeled patterns —
    leaves messy/Hinglish/conversational forms to the LLM."""
    hints = IdentityHints()
    if not text:
        return hints

    name_match = _NAME_RE.search(text)
    if name_match:
        candidate = name_match.group(1).strip().rstrip(",.")
        # Defensive: skip obvious non-names like "Account ACC1001"
        if not any(tok.isupper() and any(ch.isdigit() for ch in tok) for tok in candidate.split()):
            hints.full_name = candidate

    dob_match = _DOB_RE.search(text)
    if dob_match:
        parsed, ambiguous = _parse_date(dob_match.group(1))
        if parsed is not None:
            hints.dob = parsed
        elif ambiguous:
            hints.dob_ambiguous = True

    aadhaar_match = _AADHAAR_RE.search(text)
    if aadhaar_match:
        hints.aadhaar_last4 = aadhaar_match.group(1)

    pincode_match = _PINCODE_RE.search(text)
    if pincode_match:
        hints.pincode = pincode_match.group(1)

    return hints
