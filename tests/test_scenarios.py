"""
Tier 2: Scripted multi-turn scenario tests.
Mocks: OpenAI client (deterministic extraction) + httpx API calls.
Uses real FSM, real verification, real validators.
"""
from __future__ import annotations

import re
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


def smart_account_id(text: str):
    """Realistic content-aware side_effect for extract_account_id.

    The agent now runs extraction on the very first user message (the brief's
    "user volunteered everything in turn 1" rule), so unconditional
    return_value mocks would falsely fire on a bare "hi". Detect an ACC ID
    in the text instead.
    """
    m = re.search(r"ACC\d+", text or "", re.IGNORECASE)
    if m:
        return mock_account_id(m.group(0).upper())
    if any(w in (text or "").lower() for w in ("cancel", "stop", "quit", "never mind")):
        return mock_account_id(None, intent="wants_to_cancel")
    return mock_account_id(None, intent="off_topic")


def empty_identity():
    """Identity extraction that captured nothing — used for opportunistic
    LLM calls on first-turn messages that contain only an account ID."""
    from llm.schemas import IdentityExtraction
    return MagicMock(
        spec=IdentityExtraction,
        full_name=None, dob=None, dob_ambiguous=False,
        aadhaar_last4=None, pincode=None,
        user_intent="providing_info",
    )


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
@patch("agent.extract_account_id", side_effect=smart_account_id)
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
@patch("agent.extract_account_id", side_effect=smart_account_id)
def test_account_not_found(mock_eid, mock_la):
    agent = Agent()
    agent.next("hi")
    r = msg(agent.next("ACC9999"))
    assert agent._conv.state == State.AWAITING_ACCOUNT_ID  # stays for retry
    assert "find" in r.lower() or "unable" in r.lower() or "wasn't" in r.lower()


# ── Scenario 3: Verification fails 3 times ──────────────────────────────────

@patch("agent.lookup_account", return_value=mock_lookup_success("ACC1001"))
@patch("agent.extract_account_id", side_effect=smart_account_id)
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
@patch("agent.extract_account_id", side_effect=smart_account_id)
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
@patch("agent.extract_account_id", side_effect=smart_account_id)
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

@patch("agent.extract_account_id", side_effect=smart_account_id)
def test_user_cancel(mock_eid):
    agent = Agent()
    agent.next("hi")
    r = msg(agent.next("cancel"))
    assert agent._conv.state == State.USER_ABORTED
    assert "goodbye" in r.lower() or "ended" in r.lower()


# ── Scenario 7: User volunteers account + identity on turn 1 ────────────────
# Brief hard rule: "If a user opens with 'Hi, my name is Nithin, account ACC1001,
# DOB 1990-05-14, pay ₹500…' the agent must still proceed through the steps
# cleanly — store everything that was volunteered." This verifies the agent
# does NOT discard turn-1 info or re-ask for what was already provided.

@patch("agent.lookup_account", return_value=mock_lookup_success("ACC1001"))
@patch("agent.extract_account_id", side_effect=smart_account_id)
@patch("agent.extract_identity", return_value=mock_identity(
    name="Nithin Jain", dob=date(1990, 5, 14)
))
@patch("agent.extract_dob_confirmation", return_value=mock_dob_confirm(True, "confirmed"))
def test_user_volunteers_everything_turn1(mock_edc, mock_ei, mock_eid, mock_la):
    agent = Agent()
    # Turn 1: account ID + name + DOB all in one message
    r1 = msg(agent.next("hi, my account is ACC1001, name Nithin Jain, DOB May 14 1990"))
    # Lookup ran, identity was opportunistically harvested, DOB confirm prompt shown
    assert agent._conv.account_id == "ACC1001"
    assert agent._conv.provided_name == "Nithin Jain"
    assert agent._conv.awaiting_dob_confirmation is True
    assert agent._conv.state == State.AWAITING_IDENTITY

    # Turn 2: confirm the DOB — verification runs, balance announced
    msg(agent.next("yes"))
    assert agent._conv.state == State.AWAITING_AMOUNT


# ── Scenario 7b: Account ID alone on turn 1, no greeting wasted ─────────────

@patch("agent.lookup_account", return_value=mock_lookup_success("ACC1001"))
@patch("agent.extract_account_id", side_effect=smart_account_id)
@patch("agent.extract_identity", return_value=empty_identity())
def test_first_turn_account_id_only(mock_ei, mock_eid, mock_la):
    """If the user opens with just an account ID, lookup proceeds immediately
    instead of replying with a generic greeting and re-asking on turn 2."""
    agent = Agent()
    r = msg(agent.next("ACC1001"))
    # Account looked up, agent now asks for identity (no turn wasted on greeting)
    assert agent._conv.state == State.AWAITING_IDENTITY
    assert agent._conv.account_id == "ACC1001"
    assert "identity" in r.lower() or "name" in r.lower()


# ── Scenario 7c: Pure greeting on turn 1 returns greeting ───────────────────

@patch("agent.extract_account_id", side_effect=smart_account_id)
def test_first_turn_pure_greeting(mock_eid):
    agent = Agent()
    r = msg(agent.next("hi"))
    assert agent._conv.state == State.AWAITING_ACCOUNT_ID
    # Should be the greeting that asks for account ID
    assert "account" in r.lower()


# ── Scenario 8: Ambiguous DOB ────────────────────────────────────────────────

@patch("agent.lookup_account", return_value=mock_lookup_success("ACC1001"))
@patch("agent.extract_account_id", side_effect=smart_account_id)
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
@patch("agent.extract_account_id", side_effect=smart_account_id)
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
@patch("agent.extract_account_id", side_effect=smart_account_id)
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
@patch("agent.extract_account_id", side_effect=smart_account_id)
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
    # Expired card must increment the payment retry counter (previously it
    # didn't, allowing the user to loop forever on invalid expiry).
    assert agent._conv.payment_retries == 1


# ── Scenario 11b: Invalid CVV increments retries (regression) ───────────────

@patch("agent.lookup_account", return_value=mock_lookup_success("ACC1001"))
@patch("agent.extract_account_id", side_effect=smart_account_id)
@patch("agent.extract_identity", side_effect=[
    mock_identity(name="Nithin Jain"),
    mock_identity(dob=date(1990, 5, 14)),
])
@patch("agent.extract_dob_confirmation", return_value=mock_dob_confirm(True, "confirmed"))
@patch("agent.extract_amount", return_value=mock_amount(Decimal("500.00")))
@patch("agent.extract_card", return_value=mock_card(
    number="4532015112830366", cvv="12", month=12, year=2027, cardholder="Nithin Jain"  # CVV too short
))
def test_invalid_cvv_increments_retries(mock_ec, mock_ea, mock_edc, mock_ei, mock_eid, mock_la):
    agent = Agent()
    agent.next("hi"); agent.next("ACC1001"); agent.next("Nithin Jain")
    agent.next("DOB"); agent.next("yes"); agent.next("500")
    r = msg(agent.next("4532015112830366, exp 12/2027, CVV 12, Nithin Jain"))
    assert agent._conv.state == State.AWAITING_CARD
    assert "cvv" in r.lower()
    assert agent._conv.payment_retries == 1


# ── Scenario 11c: Card validation retries exhaust → TERMINAL_PAYMENT_FAILED ──

@patch("agent.lookup_account", return_value=mock_lookup_success("ACC1001"))
@patch("agent.extract_account_id", side_effect=smart_account_id)
@patch("agent.extract_identity", side_effect=[
    mock_identity(name="Nithin Jain"),
    mock_identity(dob=date(1990, 5, 14)),
])
@patch("agent.extract_dob_confirmation", return_value=mock_dob_confirm(True, "confirmed"))
@patch("agent.extract_amount", return_value=mock_amount(Decimal("500.00")))
@patch("agent.extract_card", side_effect=[
    # Three consecutive invalid CVVs → exhaust payment retries
    mock_card(number="4532015112830366", cvv="12", month=12, year=2027, cardholder="Nithin Jain"),
    mock_card(number="4532015112830366", cvv="1", month=12, year=2027, cardholder="Nithin Jain"),
    mock_card(number="4532015112830366", cvv="ab", month=12, year=2027, cardholder="Nithin Jain"),
])
def test_card_retries_exhausted_terminal(mock_ec, mock_ea, mock_edc, mock_ei, mock_eid, mock_la):
    agent = Agent()
    agent.next("hi"); agent.next("ACC1001"); agent.next("Nithin Jain")
    agent.next("DOB"); agent.next("yes"); agent.next("500")
    agent.next("bad cvv 1")
    agent.next("bad cvv 2")
    r = msg(agent.next("bad cvv 3"))
    assert agent._conv.state == State.TERMINAL_PAYMENT_FAILED
    assert "unable" in r.lower() or "sorry" in r.lower()


# ── Scenario 11d: After CVV error, user only re-enters CVV ──────────────────
# Tests that the centralized error handler preserves non-offending fields so
# the user doesn't have to retype the entire card.

@patch("agent.lookup_account", return_value=mock_lookup_success("ACC1001"))
@patch("agent.process_payment", return_value=mock_payment_success())
@patch("agent.extract_account_id", side_effect=smart_account_id)
@patch("agent.extract_identity", side_effect=[
    mock_identity(name="Nithin Jain"),
    mock_identity(dob=date(1990, 5, 14)),
])
@patch("agent.extract_dob_confirmation", return_value=mock_dob_confirm(True, "confirmed"))
@patch("agent.extract_amount", return_value=mock_amount(Decimal("500.00")))
@patch("agent.extract_card", side_effect=[
    # First: full card with invalid CVV
    mock_card(number="4532015112830366", cvv="12", month=12, year=2027, cardholder="Nithin Jain"),
    # Second: only the corrected CVV (other fields should carry over from state)
    mock_card(cvv="123"),
])
def test_cvv_error_preserves_other_fields(mock_ec, mock_ea, mock_edc, mock_ei, mock_eid, mock_pp, mock_la):
    agent = Agent()
    agent.next("hi"); agent.next("ACC1001"); agent.next("Nithin Jain")
    agent.next("DOB"); agent.next("yes"); agent.next("500")
    agent.next("full card with bad CVV")
    # Now user only sends the corrected CVV — payment should succeed because
    # card_number, expiry, and cardholder_name are still in state.
    r = msg(agent.next("CVV is 123"))
    assert agent._conv.state == State.CONFIRM_AND_CLOSE
    assert "txn_TEST123" in r


# ── Scenario 12: Out-of-order info (name provided before asked) ──────────────

@patch("agent.lookup_account", return_value=mock_lookup_success("ACC1001"))
@patch("agent.process_payment", return_value=mock_payment_success())
@patch("agent.extract_account_id", side_effect=smart_account_id)
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
@patch("agent.extract_account_id", side_effect=smart_account_id)
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


# ── Scenario 14: Transient LLM error doesn't crash the loop ─────────────────

@patch("agent.extract_account_id", side_effect=RuntimeError("OpenAI 503"))
def test_transient_llm_error_does_not_crash(mock_eid):
    """A raised exception from an extractor (network blip, schema bug, etc.)
    must be caught — next() returns a graceful retry message rather than
    propagating. State and retry counters stay unchanged so the user can
    just repeat their last message."""
    agent = Agent()
    r = msg(agent.next("ACC1001"))
    # Did not crash, returned a graceful prompt
    assert "hiccup" in r.lower() or "repeat" in r.lower() or "try" in r.lower()
    # State unchanged from INIT-after-greeting baseline; retry counter untouched
    assert agent._conv.account_lookup_retries == 0
    # User can retry — second attempt should succeed if the extractor recovers
    with patch("agent.extract_account_id", side_effect=smart_account_id), \
         patch("agent.lookup_account", return_value=mock_lookup_success("ACC1001")):
        r2 = msg(agent.next("ACC1001"))
    assert agent._conv.state == State.AWAITING_IDENTITY


# ── Scenario 15: LLM error during identity extraction recovers gracefully ───

@patch("agent.lookup_account", return_value=mock_lookup_success("ACC1001"))
@patch("agent.extract_account_id", side_effect=smart_account_id)
@patch("agent.extract_identity", side_effect=RuntimeError("OpenAI timeout"))
def test_identity_extraction_error_recovers(mock_ei, mock_eid, mock_la):
    agent = Agent()
    agent.next("hi")
    agent.next("ACC1001")
    assert agent._conv.state == State.AWAITING_IDENTITY
    r = msg(agent.next("Nithin Jain, DOB 14 May 1990"))
    assert "hiccup" in r.lower() or "repeat" in r.lower()
    # Verification retry counter untouched — this is a tech failure, not a
    # user-provided-wrong-info failure
    assert agent._conv.verification_retries == 0
    assert agent._conv.state == State.AWAITING_IDENTITY


# ── Scenario 16: Lookup API unexpected exception → graceful terminal ────────

@patch("agent.lookup_account", side_effect=ValueError("malformed JSON"))
@patch("agent.extract_account_id", side_effect=smart_account_id)
def test_lookup_unexpected_exception(mock_eid, mock_la):
    """If lookup_account raises something other than ServerError (e.g. a
    JSON decode bug), we still cleanly terminate instead of stranding the
    agent in LOOKING_UP_ACCOUNT."""
    agent = Agent()
    agent.next("hi")
    r = msg(agent.next("ACC1001"))
    assert agent._conv.state == State.TERMINAL_ACCOUNT_NOT_FOUND
    assert "unable" in r.lower() or "contact" in r.lower() or "notice" in r.lower()


# ── Scenario 17: Payment API unexpected exception → retryable server_error ──

@patch("agent.lookup_account", return_value=mock_lookup_success("ACC1001"))
@patch("agent.process_payment", side_effect=ValueError("bad json"))
@patch("agent.extract_account_id", side_effect=smart_account_id)
@patch("agent.extract_identity", side_effect=[
    mock_identity(name="Nithin Jain"),
    mock_identity(dob=date(1990, 5, 14)),
])
@patch("agent.extract_dob_confirmation", return_value=mock_dob_confirm(True, "confirmed"))
@patch("agent.extract_amount", return_value=mock_amount(Decimal("500.00")))
@patch("agent.extract_card", return_value=mock_card(
    number="4532015112830366", cvv="123", month=12, year=2027, cardholder="Nithin Jain"
))
def test_payment_unexpected_exception(mock_ec, mock_ea, mock_edc, mock_ei, mock_eid, mock_pp, mock_la):
    """A library-level bug in process_payment routes through the existing
    retry path rather than crashing the loop."""
    agent = Agent()
    agent.next("hi"); agent.next("ACC1001"); agent.next("Nithin Jain")
    agent.next("DOB 14 May 1990"); agent.next("yes"); agent.next("500")
    r = msg(agent.next("full card"))
    # Treated as retryable server_error — back to AWAITING_CARD, retry incremented
    assert agent._conv.state == State.AWAITING_CARD
    assert agent._conv.payment_retries == 1


# ── Scenario 18: Missing OPENAI_API_KEY raises a clear error, not KeyError ──

def test_missing_api_key_clear_error(monkeypatch):
    import importlib
    import llm.client as client_module
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(client_module, "_client", None)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        client_module.get_client()
