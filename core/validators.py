from __future__ import annotations

from datetime import date
from decimal import Decimal


def luhn_check(card_number: str) -> bool:
    """Standard Luhn algorithm."""
    digits = [int(d) for d in card_number if d.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    total = 0
    for i, digit in enumerate(reversed(digits)):
        if i % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def validate_card_number(card_number: str) -> str | None:
    """Returns normalized card number or None if invalid."""
    digits = "".join(c for c in card_number if c.isdigit())
    if not luhn_check(digits):
        return None
    return digits


def validate_cvv(cvv: str, card_number: str = "") -> bool:
    """3 digits standard, 4 for Amex (starting with 34 or 37)."""
    digits = "".join(c for c in cvv if c.isdigit())
    card_digits = "".join(c for c in card_number if c.isdigit())
    is_amex = card_digits[:2] in ("34", "37")
    expected_len = 4 if is_amex else 3
    return len(digits) == expected_len


def validate_expiry(month: int, year: int) -> bool:
    """Card must not be expired (compare to current month)."""
    today = date.today()
    if year < today.year:
        return False
    if year == today.year and month < today.month:
        return False
    if not (1 <= month <= 12):
        return False
    return True


def validate_amount(amount: Decimal, balance: Decimal) -> str | None:
    """
    Returns None if valid, or an error code string if not.
    Error codes mirror API error codes for consistency.
    """
    if amount <= 0:
        return "invalid_amount"
    # More than 2 decimal places
    if amount != amount.quantize(Decimal("0.01")):
        return "invalid_amount"
    if amount > balance:
        return "insufficient_balance"
    return None


def validate_pincode(pincode: str) -> bool:
    digits = "".join(c for c in pincode if c.isdigit())
    return len(digits) == 6


def validate_aadhaar_last4(value: str) -> bool:
    digits = "".join(c for c in value if c.isdigit())
    return len(digits) == 4


def is_valid_date(year: int, month: int, day: int) -> bool:
    try:
        date(year, month, day)
        return True
    except ValueError:
        return False
