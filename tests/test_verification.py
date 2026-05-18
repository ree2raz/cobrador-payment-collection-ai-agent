"""
Truth table tests for verification logic (16+ cases).
All deterministic — no LLM, no API.
"""
import pytest
from datetime import date
from decimal import Decimal

from core.state_machine import AccountRecord, ConversationState, State
from core.verification import verify_identity


def make_state(
    name: str | None,
    dob: date | None,
    aadhaar: str | None,
    pincode: str | None,
) -> ConversationState:
    conv = ConversationState(state=State.VERIFYING)
    conv.account = AccountRecord(
        account_id="ACC1001",
        full_name="Nithin Jain",
        dob=date(1990, 5, 14),
        aadhaar_last4="4321",
        pincode="400001",
        balance=Decimal("1250.75"),
    )
    conv.provided_name = name
    conv.provided_dob = dob
    conv.provided_aadhaar4 = aadhaar
    conv.provided_pincode = pincode
    return conv


ACCOUNT_DOB = date(1990, 5, 14)
ACCOUNT_AADHAAR = "4321"
ACCOUNT_PINCODE = "400001"
WRONG_DOB = date(1991, 1, 1)
WRONG_AADHAAR = "9999"
WRONG_PINCODE = "111111"


# 16-row truth table: name × dob × aadhaar × pincode
@pytest.mark.parametrize("name,dob,aadhaar,pincode,expected", [
    # name=correct, all secondary combos
    ("Nithin Jain", ACCOUNT_DOB,   ACCOUNT_AADHAAR, ACCOUNT_PINCODE, True),
    ("Nithin Jain", ACCOUNT_DOB,   ACCOUNT_AADHAAR, None,            True),
    ("Nithin Jain", ACCOUNT_DOB,   None,            ACCOUNT_PINCODE, True),
    ("Nithin Jain", ACCOUNT_DOB,   None,            None,            True),
    ("Nithin Jain", None,          ACCOUNT_AADHAAR, ACCOUNT_PINCODE, True),
    ("Nithin Jain", None,          ACCOUNT_AADHAAR, None,            True),
    ("Nithin Jain", None,          None,            ACCOUNT_PINCODE, True),
    ("Nithin Jain", None,          None,            None,            False),  # name only
    # name=wrong, all secondary combos — all must fail
    ("Nithin J",    ACCOUNT_DOB,   ACCOUNT_AADHAAR, ACCOUNT_PINCODE, False),
    ("nithin jain", ACCOUNT_DOB,   None,            None,            False),  # case-sensitive — brief forbids case-insensitive workaround
    ("Nithin Jain ", ACCOUNT_DOB,  None,            None,            True),   # trailing space normalizes
    (None,          ACCOUNT_DOB,   ACCOUNT_AADHAAR, ACCOUNT_PINCODE, False),  # no name
    ("Nithin Jain", WRONG_DOB,     WRONG_AADHAAR,   WRONG_PINCODE,  False),
    ("Nithin Jain", WRONG_DOB,     None,            None,            False),
    ("Nithin Jain", None,          WRONG_AADHAAR,   None,            False),
    ("Nithin Jain", None,          None,            WRONG_PINCODE,   False),
])
def test_verification_truth_table(name, dob, aadhaar, pincode, expected):
    conv = make_state(name, dob, aadhaar, pincode)
    result = verify_identity(conv)
    assert result.verified is expected, (
        f"name={name!r} dob={dob} aadhaar={aadhaar} pincode={pincode} "
        f"expected={expected} got={result.verified}"
    )


# Spec-specific account tests
def test_acc1001_dob():
    conv = make_state("Nithin Jain", date(1990, 5, 14), None, None)
    assert verify_identity(conv).verified is True


def test_acc1001_aadhaar():
    conv = make_state("Nithin Jain", None, "4321", None)
    assert verify_identity(conv).verified is True


def test_acc1001_pincode():
    conv = make_state("Nithin Jain", None, None, "400001")
    assert verify_identity(conv).verified is True


def test_acc1001_wrong_name_right_secondary():
    conv = make_state("Nithin", None, "4321", None)
    assert verify_identity(conv).verified is False


def test_leap_year_acc1004():
    conv = ConversationState(state=State.VERIFYING)
    conv.account = AccountRecord(
        account_id="ACC1004",
        full_name="Rahul Mehta",
        dob=date(1988, 2, 29),
        aadhaar_last4="1357",
        pincode="400004",
        balance=Decimal("3200.50"),
    )
    conv.provided_name = "Rahul Mehta"
    conv.provided_dob = date(1988, 2, 29)
    result = verify_identity(conv)
    assert result.verified is True


def test_leap_year_off_by_one_fails():
    conv = ConversationState(state=State.VERIFYING)
    conv.account = AccountRecord(
        account_id="ACC1004",
        full_name="Rahul Mehta",
        dob=date(1988, 2, 29),
        aadhaar_last4="1357",
        pincode="400004",
        balance=Decimal("3200.50"),
    )
    conv.provided_name = "Rahul Mehta"
    conv.provided_dob = date(1988, 2, 28)  # close but wrong
    result = verify_identity(conv)
    assert result.verified is False


def test_long_name_acc1002():
    conv = ConversationState(state=State.VERIFYING)
    conv.account = AccountRecord(
        account_id="ACC1002",
        full_name="Rajarajeswari Balasubramaniam",
        dob=date(1985, 11, 23),
        aadhaar_last4="9876",
        pincode="400002",
        balance=Decimal("540.00"),
    )
    conv.provided_name = "Rajarajeswari Balasubramaniam"
    conv.provided_aadhaar4 = "9876"
    result = verify_identity(conv)
    assert result.verified is True
