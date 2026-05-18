"""
Unit tests for core/identity_regex.py — the deterministic identity
pre-extractor that backs up the LLM on dense compound first-turn messages.

Coverage philosophy: one canonical case per regex branch, plus one
false-positive guard per field. Near-duplicate phrasings that hit the
same branch (e.g. "name X", "name: X", "name is X") add no signal —
trimmed in favor of breadth over redundancy.
"""
from __future__ import annotations

from datetime import date

import pytest

from core.identity_regex import extract_identity_hints


# ── Name capture ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("my name is Nithin Jain", "Nithin Jain"),
    ("i am Nithin Jain", "Nithin Jain"),
    ("I'm Rahul Mehta", "Rahul Mehta"),
    # Long single-name edge case — must not be truncated:
    ("my name is Rajarajeswari Balasubramaniam", "Rajarajeswari Balasubramaniam"),
])
def test_name_capture(text, expected):
    assert extract_identity_hints(text).full_name == expected


@pytest.mark.parametrize("text", [
    "hello there",
    "ACC1001 is my account",
    "name?",  # bare keyword, no name follows
])
def test_name_no_false_positive(text):
    assert extract_identity_hints(text).full_name is None


# ── DOB capture ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    # Brief's literal example phrasings:
    ("DOB 14th May 1990", date(1990, 5, 14)),
    ("date of birth 14/05/1990", date(1990, 5, 14)),
    ("d.o.b 14-05-1990", date(1990, 5, 14)),
    # ISO format:
    ("DOB 1990-05-14", date(1990, 5, 14)),
    # Leap year edge case (ACC1004):
    ("DOB 29 February 1988", date(1988, 2, 29)),
])
def test_dob_capture(text, expected):
    assert extract_identity_hints(text).dob == expected


def test_dob_ambiguous_flagged():
    hints = extract_identity_hints("DOB 01-02-1990")
    assert hints.dob is None
    assert hints.dob_ambiguous is True


@pytest.mark.parametrize("text", [
    "hello there",
    "born and raised here",  # has "born" keyword but no date
])
def test_dob_no_false_positive(text):
    hints = extract_identity_hints(text)
    assert hints.dob is None
    assert hints.dob_ambiguous is False


# ── Aadhaar capture ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Aadhaar last 4 is 9876", "9876"),
    ("aadhaar ending 9876", "9876"),
    ("Aadhaar 9876", "9876"),  # bare 4-digit after keyword
])
def test_aadhaar_capture(text, expected):
    assert extract_identity_hints(text).aadhaar_last4 == expected


def test_aadhaar_full_12_digit_not_captured_by_regex():
    # Full 12-digit Aadhaar must go through the LLM extractor which has
    # explicit "extract only the last 4" handling. Regex must NEVER retain
    # the first 8 digits in any intermediate buffer.
    hints = extract_identity_hints("my Aadhaar is 1234 5678 9876")
    assert hints.aadhaar_last4 in ("1234", "9876", None)


# ── Pincode capture ──────────────────────────────────────────────────────────

def test_pincode_capture():
    assert extract_identity_hints("pincode 400001").pincode == "400001"


def test_pincode_5_digit_rejected():
    assert extract_identity_hints("pincode 40000").pincode is None


# ── Birthday-keyword variants (DOB synonyms) ─────────────────────────────────

def test_birthday_keyword_variant():
    # "birth date" / "birthday" are common synonyms the regex must catch.
    assert extract_identity_hints("birthday: 14/05/1990").dob == date(1990, 5, 14)


# ── The motivating case: dense compound first-turn ──────────────────────────

def test_compound_first_turn_full_extraction():
    """The exact message turn1_volunteer sends — must yield both name and
    DOB deterministically without any LLM call. This is the case the
    entire regex pre-extractor exists to handle."""
    hints = extract_identity_hints(
        "Hi, my account is ACC1001, name Nithin Jain, DOB 14th May 1990, "
        "I want to pay 400 rupees"
    )
    assert hints.full_name == "Nithin Jain"
    assert hints.dob == date(1990, 5, 14)
    assert hints.aadhaar_last4 is None
    assert hints.pincode is None
    assert hints.any_captured() is True


def test_empty_input():
    hints = extract_identity_hints("")
    assert hints.any_captured() is False
