"""
Messy extraction test cases — real-world input patterns from production call centers.

Shared between:
  tests/test_extraction_messy.py  (pytest integration tests)
  eval/run_eval.py --messy         (accuracy table in the eval runner)
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from datetime import date
from decimal import Decimal
from typing import Any


@dataclass
class MessyCase:
    group: str       # extractor group: account_id | name | dob | aadhaar | pincode | amount | card
    label: str       # short description, used as pytest ID and table row label
    input_text: str  # the raw messy user message
    check_field: str # field name on the extraction result to assert
    expected: Any    # expected value of that field
    extra: dict = dc_field(default_factory=dict)  # extra extractor args (e.g. balance)


MESSY_CASES: list[MessyCase] = [
    # ── Account ID ──────────────────────────────────────────────────────────────
    MessyCase("account_id", "lowercase spaced",
              "it's acc 1001", "account_id", "ACC1001"),
    MessyCase("account_id", "hyphenated",
              "ACC-1001 is my account", "account_id", "ACC1001"),
    MessyCase("account_id", "hesitant filler",
              "my account I think... it's ACC 1001 yeah", "account_id", "ACC1001"),
    MessyCase("account_id", "hinglish",
              "haan mera account ACC1001 hai", "account_id", "ACC1001"),

    # ── Name ────────────────────────────────────────────────────────────────────
    MessyCase("name", "filler words",
              "yes sir my name is Nithin Jain", "full_name", "Nithin Jain"),
    MessyCase("name", "hinglish naam",
              "naam Nithin Jain hai", "full_name", "Nithin Jain"),
    MessyCase("name", "self-correction",
              "my name is Rahul, wait no, Nithin Jain actually", "full_name", "Nithin Jain"),
    MessyCase("name", "honorific stripped",
              "Mr. Nithin Jain here", "full_name", "Nithin Jain"),
    MessyCase("name", "compound first turn",
              "Hi, my account is ACC1001, name Nithin Jain, DOB 14th May 1990, I want to pay 400 rupees",
              "full_name", "Nithin Jain"),
    MessyCase("name", "name repetition",
              # Brief page 2: "it's Nithin, Nithin Jain"
              "it's Nithin, Nithin Jain", "full_name", "Nithin Jain"),
    MessyCase("name", "nickname plus full",
              # Brief page 2: "you can call me Raja but my full name is Rajarajeswari Balasubramaniam"
              # Must extract the FULL name, not the nickname.
              "you can call me Raja but my full name is Rajarajeswari Balasubramaniam",
              "full_name", "Rajarajeswari Balasubramaniam"),

    # ── DOB ─────────────────────────────────────────────────────────────────────
    MessyCase("dob", "two-digit year",
              # Brief page 2: "DOB is May 14, 90" → 1990-05-14
              "DOB is May 14, 90", "dob", date(1990, 5, 14)),
    MessyCase("dob", "hinglish dob",
              "DOB hai 14 may 1990", "dob", date(1990, 5, 14)),
    MessyCase("dob", "DD-MM-YYYY",
              "14-5-1990", "dob", date(1990, 5, 14)),
    MessyCase("dob", "ambiguous flagged",
              "01-02-1990", "dob_ambiguous", True),
    MessyCase("dob", "leap year verbal",
              "29th february 1988", "dob", date(1988, 2, 29)),
    MessyCase("dob", "born-on natural",
              # Brief page 2: "I was born on 14th May 1990"
              "I was born on 14th May 1990", "dob", date(1990, 5, 14)),
    MessyCase("dob", "compound first turn",
              "Hi, my account is ACC1001, name Nithin Jain, DOB 14th May 1990, I want to pay 400 rupees",
              "dob", date(1990, 5, 14)),

    # ── Aadhaar ──────────────────────────────────────────────────────────────────
    MessyCase("aadhaar", "full 12-digit",
              "my full Aadhaar is 123456789876", "aadhaar_last4", "9876"),
    MessyCase("aadhaar", "ends-with question form",
              # Brief page 2: "Aadhaar ends with 9876, shall I give pincode instead?"
              "Aadhaar ends with 9876, shall I give pincode instead?",
              "aadhaar_last4", "9876"),
    MessyCase("aadhaar", "last-four labeled",
              # Brief page 2: "last four of my Aadhaar is 4321"
              "last four of my Aadhaar is 4321", "aadhaar_last4", "4321"),

    # ── Pincode ──────────────────────────────────────────────────────────────────
    MessyCase("pincode", "spaced single digits",
              # Brief page 2: "pincode? it's 4 0 0 0 0 1"
              # The pincode normalizer strips spaces; 6 single digits with
              # spaces between them must yield the canonical 6-digit form.
              "pincode? it's 4 0 0 0 0 1", "pincode", "400001"),

    # ── Amount ───────────────────────────────────────────────────────────────────
    MessyCase("amount", "words rupees",
              "just take five hundred rupees", "amount", Decimal("500"),
              {"balance": Decimal("2000")}),
    MessyCase("amount", "rupee symbol",
              "₹500 please", "amount", Decimal("500"),
              {"balance": Decimal("2000")}),
    MessyCase("amount", "pay it all",
              "pay it all", "wants_full_balance", True,
              {"balance": Decimal("2000")}),
    MessyCase("amount", "verbal thousand",
              # Brief page 2: "I want to pay a thousand rupees"
              "I want to pay a thousand rupees", "amount", Decimal("1000"),
              {"balance": Decimal("2000")}),
    MessyCase("amount", "hesitant phrasing",
              # Brief page 2: "can I do 500 for now?"
              "can I do 500 for now?", "amount", Decimal("500"),
              {"balance": Decimal("2000")}),

    # ── Card ─────────────────────────────────────────────────────────────────────
    MessyCase("card", "spaced card number",
              "4532 0151 1283 0366", "card_number", "4532015112830366"),
    MessyCase("card", "verbal CVV",
              "CVV is one two three", "cvv", "123"),
    MessyCase("card", "verbal expiry month",
              "expires December 2027", "expiry_month", 12),
    MessyCase("card", "two-digit expiry year",
              # Brief page 2: "12/27" — must parse year=2027, not 2027 ambiguous
              "expires 12/27", "expiry_year", 2027),
]


def run_case(case: MessyCase) -> Any:
    """Dispatch a MessyCase to the appropriate extractor and return the result."""
    from llm.extractors import extract_account_id, extract_identity, extract_amount, extract_card

    if case.group == "account_id":
        return extract_account_id(case.input_text)
    elif case.group in ("name", "dob", "aadhaar", "pincode"):
        return extract_identity(case.input_text, {})
    elif case.group == "amount":
        balance = case.extra.get("balance", Decimal("2000"))
        return extract_amount(case.input_text, balance)
    elif case.group == "card":
        return extract_card(case.input_text, case.extra.get("already_collected", {}))
    else:
        raise ValueError(f"Unknown group: {case.group!r}")
