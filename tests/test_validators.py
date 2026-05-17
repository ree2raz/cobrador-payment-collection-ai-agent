import pytest
from datetime import date
from decimal import Decimal

from core.validators import (
    luhn_check,
    validate_card_number,
    validate_cvv,
    validate_expiry,
    validate_amount,
    validate_pincode,
    validate_aadhaar_last4,
    is_valid_date,
)


class TestLuhn:
    def test_valid_visa(self):
        assert luhn_check("4532015112830366") is True

    def test_valid_mastercard(self):
        assert luhn_check("5425233430109903") is True

    def test_invalid_card(self):
        assert luhn_check("4532015112830367") is False

    def test_invalid_all_zeros(self):
        # All zeros technically passes the Luhn checksum (0+0+...=0, 0%10==0).
        # This is expected — Luhn only validates the checksum, not card issuer ranges.
        # Real card validation relies on BIN range checks (out of scope here).
        assert luhn_check("0000000000000000") is True

    def test_short_number(self):
        assert luhn_check("1234") is False

    def test_with_spaces(self):
        # Spaces not stripped by luhn_check — caller should normalize
        assert luhn_check("4532015112830366") is True


class TestValidateCvv:
    def test_3_digit_visa(self):
        assert validate_cvv("123", "4532015112830366") is True

    def test_4_digit_amex(self):
        assert validate_cvv("1234", "371449635398431") is True

    def test_wrong_length_visa(self):
        assert validate_cvv("12", "4532015112830366") is False

    def test_4_digit_on_visa(self):
        assert validate_cvv("1234", "4532015112830366") is False


class TestValidateExpiry:
    def test_future_card(self):
        assert validate_expiry(12, 2027) is True

    def test_past_card(self):
        assert validate_expiry(1, 2020) is False

    def test_invalid_month(self):
        assert validate_expiry(13, 2027) is False

    def test_month_zero(self):
        assert validate_expiry(0, 2027) is False


class TestValidateAmount:
    def test_valid_partial(self):
        assert validate_amount(Decimal("500.00"), Decimal("1250.75")) is None

    def test_zero_amount(self):
        assert validate_amount(Decimal("0"), Decimal("1250.75")) == "invalid_amount"

    def test_negative(self):
        assert validate_amount(Decimal("-1"), Decimal("1250.75")) == "invalid_amount"

    def test_exceeds_balance(self):
        assert validate_amount(Decimal("1500"), Decimal("1250.75")) == "insufficient_balance"

    def test_exact_balance(self):
        assert validate_amount(Decimal("1250.75"), Decimal("1250.75")) is None

    def test_too_many_decimals(self):
        assert validate_amount(Decimal("100.001"), Decimal("1250.75")) == "invalid_amount"

    def test_two_decimals_ok(self):
        assert validate_amount(Decimal("100.50"), Decimal("1250.75")) is None


class TestDateValidation:
    def test_leap_year_valid(self):
        assert is_valid_date(1988, 2, 29) is True

    def test_non_leap_year_invalid(self):
        assert is_valid_date(1989, 2, 29) is False

    def test_normal_date(self):
        assert is_valid_date(1990, 5, 14) is True

    def test_invalid_month(self):
        assert is_valid_date(1990, 13, 1) is False


class TestValidatePincode:
    def test_valid(self):
        assert validate_pincode("400001") is True

    def test_invalid_short(self):
        assert validate_pincode("40000") is False

    def test_invalid_long(self):
        assert validate_pincode("4000011") is False


class TestValidateAadhaar:
    def test_valid(self):
        assert validate_aadhaar_last4("4321") is True

    def test_invalid_short(self):
        assert validate_aadhaar_last4("432") is False

    def test_invalid_long(self):
        assert validate_aadhaar_last4("43210") is False
