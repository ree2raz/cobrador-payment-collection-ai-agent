"""
Tests for event_log card / CVV masking.

The brief explicitly forbids logging raw card data beyond what is needed
for the API call. event_log.py provides the masking primitives used by
agent.py (turn_start.user_input) and llm/extractors.py (llm_extract
input + output). These tests pin those guarantees so a future refactor
can't silently regress to logging raw cards.
"""
from __future__ import annotations

import pytest

from event_log import (
    EVENT_LLM_EXTRACT,
    mask_card_number,
    mask_card_substrings,
    mask_cvv_substrings,
)


# ── Card-number masking ──────────────────────────────────────────────────────

class TestMaskCardNumber:
    def test_16_digit_masked_to_last_4(self):
        assert mask_card_number("4532015112830366") == "****0366"

    def test_15_digit_amex_masked_to_last_4(self):
        assert mask_card_number("378282246310005") == "****0005"

    def test_short_returns_stars(self):
        assert mask_card_number("123") == "****"

    def test_empty_returns_empty(self):
        assert mask_card_number("") == ""
        assert mask_card_number(None) == ""


class TestMaskCardSubstrings:
    def test_finds_card_in_sentence(self):
        out = mask_card_substrings("card is 4532015112830366 expires 12/27")
        assert "4532015112830366" not in out
        assert "****0366" in out

    def test_handles_spaced_card_number(self):
        out = mask_card_substrings("card 4532 0151 1283 0366")
        assert "4532015112830366" not in out
        assert "0151" not in out  # interior digits also masked
        assert "****0366" in out

    def test_handles_hyphenated_card_number(self):
        out = mask_card_substrings("4532-0151-1283-0366")
        assert "0151" not in out
        assert "****0366" in out

    def test_preserves_short_digit_sequences(self):
        # 4-digit Aadhaar, 6-digit pincode — must NOT be masked as cards.
        assert mask_card_substrings("aadhaar 9876 pincode 400001") == (
            "aadhaar 9876 pincode 400001"
        )

    def test_preserves_phone_like_10_digit(self):
        # 10 digits is below the 12-digit card threshold — leave alone.
        out = mask_card_substrings("call me at 9876543210")
        assert "9876543210" in out

    def test_empty_string_passes_through(self):
        assert mask_card_substrings("") == ""

    def test_no_digits_passes_through(self):
        assert mask_card_substrings("hello there") == "hello there"


class TestMaskCvvSubstrings:
    def test_numeric_cvv_masked(self):
        out = mask_cvv_substrings("CVV is 123")
        assert "123" not in out
        assert "CVV ***" in out

    def test_numeric_cvv_4_digit_amex_masked(self):
        out = mask_cvv_substrings("cvv 1234")
        assert "1234" not in out

    def test_verbal_cvv_3_digits_masked(self):
        out = mask_cvv_substrings("CVV one two three")
        assert "one two three" not in out
        assert "CVV ***" in out

    def test_verbal_cvv_4_digits_masked(self):
        out = mask_cvv_substrings("cvv is one two three four")
        assert "one two three four" not in out

    def test_no_cvv_keyword_no_change(self):
        # "123" alone shouldn't be touched; CVV is only masked when the
        # word 'cvv' is present alongside it.
        assert mask_cvv_substrings("I want to pay 123 rupees") == (
            "I want to pay 123 rupees"
        )


class TestCombinedMasking:
    """End-to-end: typical card-collection user input must be fully scrubbed."""

    def test_compound_card_message_fully_masked(self):
        raw = "card 4532015112830366 expires 12/27 CVV is 123"
        out = mask_cvv_substrings(mask_card_substrings(raw))
        assert "4532015112830366" not in out
        assert "123" not in out
        assert "****0366" in out
        assert "CVV ***" in out

    def test_verbal_cvv_with_numeric_card_fully_masked(self):
        raw = "card 4532015112830366, CVV one two three, expiry december twenty seven"
        out = mask_cvv_substrings(mask_card_substrings(raw))
        assert "4532015112830366" not in out
        assert "one two three" not in out


# ── Event constants must exist (typo-safety guarantee) ───────────────────────

def test_event_constants_are_unique_strings():
    """If any two event constants collide, queries like jq 'select(.event ==
    "turn_start")' would silently match the wrong events."""
    import event_log as el
    names = [
        n for n in dir(el)
        if n.startswith("EVENT_") and isinstance(getattr(el, n), str)
    ]
    values = [getattr(el, n) for n in names]
    assert len(values) == len(set(values)), f"duplicate event constant values: {values}"
    # And one assertion that the constants resolve to the expected strings
    assert EVENT_LLM_EXTRACT == "llm_extract"
