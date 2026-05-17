"""
Tier 2: Scripted multi-turn scenario tests.
Mocks: OpenAI client (deterministic extraction) + httpx API calls.
Uses real FSM, real verification, real validators.
"""
from __future__ import annotations

import pytest
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from agent import Agent
from core.state_machine import State
from output.pii_filter import contains_pii


# ── Mock factories ───────────────────────────────────────────────────────────

def mock_account_id(account_id: str | None, intent="provided_id"):
    from llm.schemas import AccountIdExtraction
    return MagicMock(spec=AccountIdExtraction, account_id=account_id, user_intent=intent)


def mock_identity(name=None, dob=None, dob_ambiguous=False, aadhaar=None, pincode=None, intent="providing_info"):
    from llm.schemas import IdentityExtraction
    return MagicMock(
        spec=IdentityExtraction,
        full_name=name,
        dob=dob,
        dob_ambiguous=dob_ambiguous,
        aadhaar_last4=aadhaar,
        pincode=pincode,
        user_intent=intent,
    )


def mock_dob_confirm(confirmed: bool, intent: str = "confirmed"):
    from llm.schemas import DobConfirmation
    return MagicMock(spec=DobConfirmation, confirmed=confirmed, user_intent=intent)


def mock_amount(amount: Decimal | None, full_balance: bool = False, intent="providing_amount"):
    from llm.schemas import AmountExtraction
    return MagicMock(spec=AmountExtraction, amount=amount, wants_full_balance=full_balance, user_intent=intent)


def mock_card(number=None, cvv=None, month=None, year=None, cardholder=None, intent="providing_card"):
    from llm.schemas import CardExtraction
    return MagicMock(
        spec=CardExtraction,
        card_number=number,
        cvv=cvv,
        expiry_month=month,
        expiry_year=year,
        cardholder_name=cardholder,
        user_intent=intent,
    )


def mock_lookup_success(account_id="ACC1001"):
    from core.state_machine import AccountRecord
    accounts = {
        "ACC1001": AccountRecord("ACC1001", "Nithin Jain", date(1990, 5, 14), "4321", "400001", Decimal("1250.75")),
        "ACC1002": AccountRecord("ACC1002", "Rajarajeswari Balasubramaniam", date(1985, 11, 23), "9876", "400002", Decimal("540.00")),
        "ACC1003": AccountRecord("ACC1003", "Priya Agarwal", date(1992, 8, 10), "2468", "400003", Decimal("0.00")),
        "ACC1004": AccountRecord("ACC1004", "Rahul Mehta", date(1988, 2, 29), "1357", "400004", Decimal("3200.50")),
    }
    return MagicMock(success=True, account=accounts[account_id], error_code=None)


def mock_lookup_not_found():
    return MagicMock(success=False, account=None, error_code="account_not_found")


def mock_payment_success():
    return MagicMock(success=True, transaction_id="txn_TEST123", error_code=None)


def mock_payment_failure(error_code: str):
    return MagicMock(success=False, transaction_id=None, error_code=error_code)


def msg(r: dict) -> str:
    return r["message"]


# ── Scenario 1: Happy path ACC1001 ──────────────────────────────────────────

@patch("agent.lookup_account", return_value=mock_lookup_success("ACC1001"))
@patch("agent.process_payment", return_value=mock_payment_success())
@patch("agent.extract_account_id", return_value=mock_account_id("ACC1001"))
@patch("agent.extract_identity", side_effect=[
    mock_identity(name="Nithin Jain"),
    mock_identity(dob=date(1990, 5, 14)),
])
@patch("agent.extract_dob_confirmation", return_value=mock_dob_confirm(True, "confirmed"))
@patch("agent.extract_amount", return_value=mock_amount(Decimal("500.00")))
@patch("agent.extract_card", return_value=mock_card(
    number="4532015112830366", cvv="123", month=12, year=2027, cardholder="Nithin Jain"
))
def test_happy_path_acc1001(mock_ec, mock_ea, mock_edc, mock_ei, mock_eid, mock_pp, mock_la):
    agent = Agent()

    r0 = msg(agent.next("hi"))
    assert agent._conv.state == State.AWAITING_ACCOUNT_ID

    r1 = msg(agent.next("ACC1001"))
    assert agent._conv.state == State.AWAITING_IDENTITY

    r2 = msg(agent.next("Nithin Jain"))
    assert agent._conv.provided_name == "Nithin Jain"

    r3 = msg(agent.next("DOB is 14th May 1990"))
    # DOB confirm-back
    assert agent._conv.awaiting_dob_confirmation is True
    assert "confirm" in r3.lower() or "14" in r3

    r4 = msg(agent.next("yes"))
    # Verified — balance shared
    assert agent._conv.state == State.AWAITING_AMOUNT
    assert "1,250.75" in r4 or "1250.75" in r4

    r5 = msg(agent.next("pay 500"))
    assert agent._conv.state == State.AWAITING_CARD
    assert "card" in r5.lower()

    r6 = msg(agent.next("4532015112830366, expires 12/2027, CVV 123, cardholder Nithin Jain"))
    assert agent._conv.state == State.CONFIRM_AND_CLOSE
    assert "txn_TEST123" in r6
    assert "500" in r6

    # PII check on all responses
    account = agent._conv.account
    for m in [r0, r1, r2, r3, r4, r5, r6]:
        assert not contains_pii(m, account), f"PII leaked in: {m}"


# ── Scenario 2: Account not found ───────────────────────────────────────────

@patch("agent.lookup_account", return_value=mock_lookup_not_found())
@patch("agent.extract_account_id", return_value=mock_account_id("ACC9999"))
def test_account_not_found(mock_eid, mock_la):
    agent = Agent()
    agent.next("hi")
    r = msg(agent.next("ACC9999"))
    assert agent._conv.state == State.AWAITING_ACCOUNT_ID  # stays for retry
    assert "find" in r.lower() or "unable" in r.lower() or "wasn't" in r.lower()


# ── Scenario 3: Verification fails 3 times ──────────────────────────────────

@patch("agent.lookup_account", return_value=mock_lookup_success("ACC1001"))
@patch("agent.extract_account_id", return_value=mock_account_id("ACC1001"))
@patch("agent.extract_identity", return_value=mock_identity(name="Wrong Name", dob=date(1999, 1, 1)))
@patch("agent.extract_dob_confirmation", return_value=mock_dob_confirm(True, "confirmed"))
def test_verification_fails_terminal(mock_edc, mock_ei, mock_eid, mock_la):
    agent = Agent()
    agent.next("hi")
    agent.next("ACC1001")
    # First failure
    agent.next("Wrong Name")
    agent.next("yes")
    assert agent._conv.verification_retries == 1
    # Second failure
    agent.next("Wrong Name")
    agent.next("yes")
    assert agent._conv.verification_retries == 2
    # Third failure
    agent.next("Wrong Name")
    r = msg(agent.next("yes"))
    assert agent._conv.state == State.TERMINAL_VERIFICATION_FAILED
    assert "security" in r.lower() or "unable" in r.lower() or "sorry" in r.lower()


# ── Scenario 4: Zero balance (ACC1003) ──────────────────────────────────────

@patch("agent.lookup_account", return_value=mock_lookup_success("ACC1003"))
@patch("agent.extract_account_id", return_value=mock_account_id("ACC1003"))
@patch("agent.extract_identity", side_effect=[
    mock_identity(name="Priya Agarwal"),
    mock_identity(dob=date(1992, 8, 10)),
])
@patch("agent.extract_dob_confirmation", return_value=mock_dob_confirm(True, "confirmed"))
def test_zero_balance_close(mock_edc, mock_ei, mock_eid, mock_la):
    agent = Agent()
    agent.next("hi")
    agent.next("ACC1003")
    agent.next("Priya Agarwal")
    agent.next("DOB is 10th August 1992")
    r = msg(agent.next("yes"))
    # Should announce zero balance and close
    assert agent._conv.state == State.CONFIRM_AND_CLOSE
    assert "0" in r or "nothing" in r.lower() or "no" in r.lower()


# ── Scenario 5: Invalid card (Luhn fail) then success ───────────────────────

@patch("agent.lookup_account", return_value=mock_lookup_success("ACC1001"))
@patch("agent.process_payment", return_value=mock_payment_success())
@patch("agent.extract_account_id", return_value=mock_account_id("ACC1001"))
@patch("agent.extract_identity", side_effect=[
    mock_identity(name="Nithin Jain"),
    mock_identity(dob=date(1990, 5, 14)),
])
@patch("agent.extract_dob_confirmation", return_value=mock_dob_confirm(True, "confirmed"))
@patch("agent.extract_amount", return_value=mock_amount(Decimal("500.00")))
@patch("agent.extract_card", side_effect=[
    mock_card(number="4532015112830367", cvv="123", month=12, year=2027, cardholder="Nithin Jain"),  # Luhn fail
    mock_card(number="4532015112830366", cvv="123", month=12, year=2027, cardholder="Nithin Jain"),  # valid
])
def test_invalid_card_then_success(mock_ec, mock_ea, mock_edc, mock_ei, mock_eid, mock_pp, mock_la):
    agent = Agent()
    agent.next("hi")
    agent.next("ACC1001")
    agent.next("Nithin Jain")
    agent.next("DOB")
    agent.next("yes")
    agent.next("500")
    r1 = msg(agent.next("bad card number"))
    assert "invalid" in r1.lower() or "valid" in r1.lower()
    assert agent._conv.state == State.AWAITING_CARD  # stayed in card state
    r2 = msg(agent.next("4532015112830366"))
    assert agent._conv.state == State.CONFIRM_AND_CLOSE
    assert "txn_TEST123" in r2


# ── Scenario 6: User cancels mid-flow ───────────────────────────────────────

@patch("agent.extract_account_id", return_value=mock_account_id(None, intent="wants_to_cancel"))
def test_user_cancel(mock_eid):
    agent = Agent()
    agent.next("hi")
    r = msg(agent.next("cancel"))
    assert agent._conv.state == State.USER_ABORTED
    assert "goodbye" in r.lower() or "ended" in r.lower()


# ── Scenario 7: User provides everything in turn 1 ──────────────────────────

@patch("agent.lookup_account", return_value=mock_lookup_success("ACC1001"))
@patch("agent.process_payment", return_value=mock_payment_success())
@patch("agent.extract_account_id", return_value=mock_account_id("ACC1001"))
@patch("agent.extract_identity", return_value=mock_identity(
    name="Nithin Jain", dob=date(1990, 5, 14)
))
@patch("agent.extract_dob_confirmation", return_value=mock_dob_confirm(True, "confirmed"))
@patch("agent.extract_amount", return_value=mock_amount(Decimal("500.00")))
@patch("agent.extract_card", return_value=mock_card(
    number="4532015112830366", cvv="123", month=12, year=2027, cardholder="Nithin Jain"
))
def test_user_volunteers_everything(mock_ec, mock_ea, mock_edc, mock_ei, mock_eid, mock_pp, mock_la):
    agent = Agent()
    agent.next("hi, my account is ACC1001, name Nithin Jain, DOB May 14 1990")
    agent.next("ACC1001")
    # State: AWAITING_IDENTITY — extraction has name+dob already
    agent.next("Nithin Jain, DOB 14th May 1990")
    agent.next("yes")  # confirm DOB
    # Should be at AWAITING_AMOUNT
    assert agent._conv.state == State.AWAITING_AMOUNT
    assert agent._conv.provided_name == "Nithin Jain"


# ── Scenario 8: Ambiguous DOB ────────────────────────────────────────────────

@patch("agent.lookup_account", return_value=mock_lookup_success("ACC1001"))
@patch("agent.extract_account_id", return_value=mock_account_id("ACC1001"))
@patch("agent.extract_identity", side_effect=[
    mock_identity(name="Nithin Jain"),
    mock_identity(dob=None, dob_ambiguous=True),  # ambiguous
    mock_identity(dob=date(1990, 5, 14)),           # clarified
])
@patch("agent.extract_dob_confirmation", return_value=mock_dob_confirm(True, "confirmed"))
def test_ambiguous_dob(mock_edc, mock_ei, mock_eid, mock_la):
    agent = Agent()
    agent.next("hi")
    agent.next("ACC1001")
    agent.next("Nithin Jain")
    r = msg(agent.next("01-02-1990"))  # ambiguous
    assert "clarify" in r.lower() or "format" in r.lower() or "clear" in r.lower()
    r2 = msg(agent.next("14th May 1990"))  # clarified
    assert agent._conv.awaiting_dob_confirmation is True
    agent.next("yes")
    assert agent._conv.provided_dob == date(1990, 5, 14)


# ── Scenario 9: Payment API error (invalid_card) ─────────────────────────────

@patch("agent.lookup_account", return_value=mock_lookup_success("ACC1001"))
@patch("agent.process_payment", return_value=mock_payment_failure("invalid_card"))
@patch("agent.extract_account_id", return_value=mock_account_id("ACC1001"))
@patch("agent.extract_identity", side_effect=[
    mock_identity(name="Nithin Jain"),
    mock_identity(dob=date(1990, 5, 14)),
])
@patch("agent.extract_dob_confirmation", return_value=mock_dob_confirm(True, "confirmed"))
@patch("agent.extract_amount", return_value=mock_amount(Decimal("500.00")))
@patch("agent.extract_card", return_value=mock_card(
    number="4532015112830366", cvv="123", month=12, year=2027, cardholder="Nithin Jain"
))
def test_payment_invalid_card_error(mock_ec, mock_ea, mock_edc, mock_ei, mock_eid, mock_pp, mock_la):
    agent = Agent()
    agent.next("hi")
    agent.next("ACC1001")
    agent.next("Nithin Jain")
    agent.next("DOB")
    agent.next("yes")
    agent.next("500")
    r = msg(agent.next("card details"))
    assert agent._conv.state == State.AWAITING_CARD  # retryable — stays in card state
    assert "card" in r.lower() or "invalid" in r.lower()


# ── Scenario 10: Leap year ACC1004 ───────────────────────────────────────────

@patch("agent.lookup_account", return_value=mock_lookup_success("ACC1004"))
@patch("agent.process_payment", return_value=mock_payment_success())
@patch("agent.extract_account_id", return_value=mock_account_id("ACC1004"))
@patch("agent.extract_identity", side_effect=[
    mock_identity(name="Rahul Mehta"),
    mock_identity(dob=date(1988, 2, 29)),
])
@patch("agent.extract_dob_confirmation", return_value=mock_dob_confirm(True, "confirmed"))
@patch("agent.extract_amount", return_value=mock_amount(Decimal("1000.00")))
@patch("agent.extract_card", return_value=mock_card(
    number="4532015112830366", cvv="123", month=12, year=2027, cardholder="Rahul Mehta"
))
def test_leap_year_acc1004(mock_ec, mock_ea, mock_edc, mock_ei, mock_eid, mock_pp, mock_la):
    agent = Agent()
    agent.next("hi")
    agent.next("ACC1004")
    agent.next("Rahul Mehta")
    agent.next("DOB 29 Feb 1988")
    r = msg(agent.next("yes"))
    assert agent._conv.provided_dob == date(1988, 2, 29)
    assert agent._conv.state == State.AWAITING_AMOUNT
    assert "3,200.50" in r or "3200.50" in r


# ── Scenario 11: Expired card ───────────────────────────────────────────────

@patch("agent.lookup_account", return_value=mock_lookup_success("ACC1001"))
@patch("agent.extract_account_id", return_value=mock_account_id("ACC1001"))
@patch("agent.extract_identity", side_effect=[
    mock_identity(name="Nithin Jain"),
    mock_identity(dob=date(1990, 5, 14)),
])
@patch("agent.extract_dob_confirmation", return_value=mock_dob_confirm(True, "confirmed"))
@patch("agent.extract_amount", return_value=mock_amount(Decimal("500.00")))
@patch("agent.extract_card", return_value=mock_card(
    number="4532015112830366", cvv="123", month=3, year=2020, cardholder="Nithin Jain"  # expired
))
def test_expired_card(mock_ec, mock_ea, mock_edc, mock_ei, mock_eid, mock_la):
    agent = Agent()
    agent.next("hi")
    agent.next("ACC1001")
    agent.next("Nithin Jain")
    agent.next("DOB")
    agent.next("yes")
    agent.next("500")
    r = msg(agent.next("expired card"))
    assert agent._conv.state == State.AWAITING_CARD
    assert "expired" in r.lower() or "expiry" in r.lower() or "invalid" in r.lower()


# ── Scenario 12: Out-of-order info (name provided before asked) ──────────────

@patch("agent.lookup_account", return_value=mock_lookup_success("ACC1001"))
@patch("agent.process_payment", return_value=mock_payment_success())
@patch("agent.extract_account_id", return_value=mock_account_id("ACC1001"))
@patch("agent.extract_identity", side_effect=[
    # User provides name AND aadhaar in one message
    mock_identity(name="Nithin Jain", aadhaar="4321"),
])
@patch("agent.extract_amount", return_value=mock_amount(Decimal("200.00")))
@patch("agent.extract_card", return_value=mock_card(
    number="4532015112830366", cvv="123", month=12, year=2027, cardholder="Nithin Jain"
))
def test_out_of_order_info(mock_ec, mock_ea, mock_ei, mock_eid, mock_pp, mock_la):
    agent = Agent()
    agent.next("hi")
    agent.next("ACC1001")
    # User provides both name and aadhaar at once — should proceed to verification
    msg(agent.next("My name is Nithin Jain and Aadhaar last 4 is 4321"))
    assert agent._conv.provided_name == "Nithin Jain"
    assert agent._conv.provided_aadhaar4 == "4321"
    # Should go to AWAITING_AMOUNT since we have name + secondary
    assert agent._conv.state == State.AWAITING_AMOUNT


# ── Scenario 13: PII never appears in agent messages ─────────────────────────

@patch("agent.lookup_account", return_value=mock_lookup_success("ACC1001"))
@patch("agent.process_payment", return_value=mock_payment_success())
@patch("agent.extract_account_id", return_value=mock_account_id("ACC1001"))
@patch("agent.extract_identity", side_effect=[
    mock_identity(name="Nithin Jain"),
    mock_identity(dob=date(1990, 5, 14)),
])
@patch("agent.extract_dob_confirmation", return_value=mock_dob_confirm(True, "confirmed"))
@patch("agent.extract_amount", return_value=mock_amount(Decimal("500.00")))
@patch("agent.extract_card", return_value=mock_card(
    number="4532015112830366", cvv="123", month=12, year=2027, cardholder="Nithin Jain"
))
def test_pii_not_in_any_response(mock_ec, mock_ea, mock_edc, mock_ei, mock_eid, mock_pp, mock_la):
    """PII must never appear in any agent message across a full conversation."""
    agent = Agent()
    responses = []
    for user_msg in ["hi", "ACC1001", "Nithin Jain", "DOB 14 May 1990", "yes", "500",
                     "4532015112830366, CVV 123, exp 12/2027, cardholder Nithin Jain"]:
        r = msg(agent.next(user_msg))
        responses.append(r)
        if agent._conv.state.name.startswith("TERMINAL") or agent._conv.state.name == "CONFIRM_AND_CLOSE":
            break

    account = agent._conv.account
    assert account is not None

    for i, response in enumerate(responses):
        assert not contains_pii(response, account), (
            f"PII leaked in turn {i}: {response}"
        )
