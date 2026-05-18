import pytest
from datetime import date
from decimal import Decimal

from core.state_machine import AccountRecord
from output.pii_filter import contains_pii, redact_pii


def make_account() -> AccountRecord:
    return AccountRecord(
        account_id="ACC1001",
        full_name="Nithin Jain",
        dob=date(1990, 5, 14),
        aadhaar_last4="4321",
        pincode="400001",
        balance=Decimal("1250.75"),
    )


class TestContainsPii:
    def test_clean_message(self):
        acc = make_account()
        assert contains_pii("Your balance is ₹1250.75.", acc) is False

    def test_dob_iso(self):
        acc = make_account()
        assert contains_pii("Your DOB is 1990-05-14.", acc) is True

    @pytest.mark.parametrize("dob_text", [
        "14th May 1990",
        "14 May, 1990",
        "May 14th 1990",
        "May 14, 1990",
        "14/05/90",
        "05/14/90",
    ])
    def test_dob_common_variants(self, dob_text):
        acc = make_account()
        assert contains_pii(f"The date on file is {dob_text}.", acc) is True

    def test_aadhaar_last4(self):
        acc = make_account()
        assert contains_pii("Your Aadhaar ends with 4321.", acc) is True

    def test_pincode(self):
        acc = make_account()
        assert contains_pii("Your pincode is 400001.", acc) is True

    def test_none_account(self):
        assert contains_pii("anything", None) is False


class TestRedactPii:
    def test_redacts_dob(self):
        acc = make_account()
        msg = "DOB on file is 1990-05-14."
        result = redact_pii(msg, acc)
        assert "1990-05-14" not in result
        assert "[REDACTED]" in result

    def test_redacts_dob_with_ordinal_text(self):
        acc = make_account()
        msg = "DOB on file is 14th May 1990."
        result = redact_pii(msg, acc)
        assert "14th May 1990" not in result
        assert "[REDACTED]" in result

    def test_redacts_aadhaar(self):
        acc = make_account()
        msg = "Aadhaar last 4: 4321."
        result = redact_pii(msg, acc)
        assert "4321" not in result

    def test_redacts_pincode(self):
        acc = make_account()
        msg = "Pincode: 400001."
        result = redact_pii(msg, acc)
        assert "400001" not in result

    def test_clean_message_unchanged(self):
        acc = make_account()
        msg = "Your balance is ₹1,250.75."
        assert redact_pii(msg, acc) == msg

    def test_none_account(self):
        msg = "Hello there."
        assert redact_pii(msg, None) == msg
