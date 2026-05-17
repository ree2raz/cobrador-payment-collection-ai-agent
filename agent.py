"""
Payment Collection Agent — Cobrador

Architecture: Deterministic FSM owns all flow control. LLM is used only for
structured extraction of messy natural language into typed fields. Templated
responses are used for 90% of agent output (deterministic, testable, PII-safe).
LLM response generation is reserved for dynamic content (balance, confirmation).
"""
from __future__ import annotations

import logging
from decimal import Decimal

from core.state_machine import (
    CardDetails,
    ConversationState,
    State,
    TERMINAL_STATES,
)
from core.validators import (
    luhn_check,
    validate_amount,
    validate_cvv,
    validate_expiry,
)
from core.verification import verify_identity
from llm.extractors import (
    extract_account_id,
    extract_amount,
    extract_card,
    extract_dob_confirmation,
    extract_identity,
)
from output import responses as R
from output.pii_filter import redact_pii
from tools.payment_api import PaymentResult, ServerError, lookup_account, process_payment

logger = logging.getLogger(__name__)

MAX_VERIFICATION_RETRIES = 3
MAX_ACCOUNT_LOOKUP_RETRIES = 3
MAX_PAYMENT_RETRIES = 3

RETRYABLE_PAYMENT_ERRORS = {"invalid_card", "invalid_cvv", "invalid_expiry", "server_error"}


class Agent:
    def __init__(self) -> None:
        self._conv = ConversationState()

    def next(self, user_input: str) -> dict:
        """Process one turn. Returns {"message": str}.

        Wraps _process in an exception boundary: any unhandled exception
        (transient OpenAI error, network blip, schema-parse failure, etc.)
        is caught, logged, and rendered as a generic retry message. FSM
        state is left untouched so the user can simply repeat their last
        input on the next turn without burning any retry counter."""
        user_input = user_input.strip()
        try:
            response = self._process(user_input)
        except Exception as exc:
            logger.exception(
                "Unhandled error in turn (state=%s): %s", self._conv.state, exc
            )
            response = R.TRANSIENT_ERROR
        # Final PII redaction layer — defense in depth
        response = redact_pii(response, self._conv.account)
        logger.debug("state=%s response=%r", self._conv.state, response[:80])
        return {"message": response}

    # ── Main dispatch ───────────────────────────────────────────────────────

    def _process(self, user_input: str) -> str:
        state = self._conv.state

        # Absorb all input in terminal states
        if state in TERMINAL_STATES:
            return R.CLOSING if state == State.CONFIRM_AND_CLOSE else R.ABORTED

        # INIT — first turn. If the user volunteered an account ID (and possibly
        # identity fields) in the opening message, process it instead of wasting
        # a turn on a pure greeting. Brief rule: never re-ask for info the user
        # already provided. A bare greeting falls through to the greeting
        # response without counting as a failed account-lookup attempt.
        if state == State.INIT:
            self._conv.transition(State.AWAITING_ACCOUNT_ID, trigger="first_turn")
            if not user_input:
                return R.GREETING
            extraction = extract_account_id(user_input)
            if extraction.user_intent == "wants_to_cancel":
                self._conv.transition(State.USER_ABORTED, trigger="user_cancel")
                return R.ABORTED
            if extraction.account_id is None:
                # Greeting / chit-chat with no account ID — don't burn a retry
                return R.GREETING
            self._conv.account_id = extraction.account_id
            response = self._do_lookup()
            # If lookup succeeded, opportunistically harvest identity fields
            # from the same opening message before falling back to ASK_IDENTITY.
            if self._conv.state == State.AWAITING_IDENTITY:
                opportunistic = self._opportunistic_identity(user_input)
                if opportunistic is not None:
                    return opportunistic
            return response

        if state == State.AWAITING_ACCOUNT_ID:
            return self._handle_account_id(user_input)

        if state == State.AWAITING_IDENTITY:
            return self._handle_identity(user_input)

        if state == State.AWAITING_AMOUNT:
            return self._handle_amount(user_input)

        if state == State.AWAITING_CARD:
            return self._handle_card(user_input)

        # Should never reach here for valid states
        logger.error("Unhandled state %s", state)
        return R.FALLBACK

    # ── Account ID handler ──────────────────────────────────────────────────

    def _handle_account_id(self, user_input: str) -> str:
        extraction = extract_account_id(user_input)

        if extraction.user_intent == "wants_to_cancel":
            self._conv.transition(State.USER_ABORTED, trigger="user_cancel")
            return R.ABORTED

        if extraction.account_id is None:
            self._conv.account_lookup_retries += 1
            if self._conv.account_lookup_retries >= MAX_ACCOUNT_LOOKUP_RETRIES:
                self._conv.transition(State.TERMINAL_ACCOUNT_NOT_FOUND, trigger="max_id_retries")
                return R.ACCOUNT_LOOKUP_FAILED
            return R.ASK_ACCOUNT_ID

        # Have an account ID — attempt lookup
        self._conv.account_id = extraction.account_id
        return self._do_lookup()

    def _do_lookup(self) -> str:
        assert self._conv.account_id is not None
        self._conv.transition(State.LOOKING_UP_ACCOUNT, trigger="start_lookup")

        try:
            result = lookup_account(self._conv.account_id)
        except ServerError:
            self._conv.transition(State.TERMINAL_ACCOUNT_NOT_FOUND, trigger="lookup_server_error")
            return R.ACCOUNT_LOOKUP_FAILED
        except Exception as exc:
            # Defense in depth: any unexpected error (JSON decode, library bug,
            # etc.) is treated like a server error so we never strand the agent
            # in LOOKING_UP_ACCOUNT. The generic terminal message does not
            # reveal whether the failure was technical or account-not-found.
            logger.exception("lookup_account unexpected error: %s", exc)
            self._conv.transition(State.TERMINAL_ACCOUNT_NOT_FOUND, trigger="lookup_unexpected_error")
            return R.ACCOUNT_LOOKUP_FAILED

        if not result.success:
            self._conv.account_lookup_retries += 1
            if self._conv.account_lookup_retries >= MAX_ACCOUNT_LOOKUP_RETRIES:
                self._conv.transition(State.TERMINAL_ACCOUNT_NOT_FOUND, trigger="max_lookup_retries")
                return R.ACCOUNT_LOOKUP_FAILED
            self._conv.account_id = None  # clear so user must re-enter
            self._conv.transition(State.AWAITING_ACCOUNT_ID, trigger="account_not_found_retry")
            return R.ACCOUNT_NOT_FOUND

        self._conv.account = result.account
        self._conv.transition(State.AWAITING_IDENTITY, trigger="lookup_success")
        return self._ask_identity()

    # ── Identity handler ────────────────────────────────────────────────────

    def _handle_identity(self, user_input: str) -> str:
        # DOB confirmation sub-flow
        if self._conv.awaiting_dob_confirmation:
            return self._handle_dob_confirmation(user_input)

        already = {
            "full_name": self._conv.provided_name,
            "dob": self._conv.provided_dob,
            "aadhaar_last4": self._conv.provided_aadhaar4,
            "pincode": self._conv.provided_pincode,
        }
        extraction = extract_identity(user_input, already)

        if extraction.user_intent == "wants_to_cancel":
            self._conv.transition(State.USER_ABORTED, trigger="user_cancel")
            return R.ABORTED

        # Merge newly extracted fields (don't overwrite existing with None)
        if extraction.full_name is not None:
            self._conv.provided_name = extraction.full_name
        if extraction.aadhaar_last4 is not None:
            self._conv.provided_aadhaar4 = extraction.aadhaar_last4
        if extraction.pincode is not None:
            self._conv.provided_pincode = extraction.pincode

        # DOB: if extracted and unambiguous, enter confirm-back flow
        if extraction.dob is not None and not extraction.dob_ambiguous:
            self._conv.pending_dob = extraction.dob
            self._conv.awaiting_dob_confirmation = True
            return R.dob_confirm_prompt(extraction.dob)

        if extraction.dob_ambiguous:
            return R.dob_ambiguous_prompt()

        # Check if we have everything needed
        if self._conv.has_enough_identity():
            return self._do_verification()

        return self._ask_identity()

    def _handle_dob_confirmation(self, user_input: str) -> str:
        assert self._conv.pending_dob is not None
        confirmation = extract_dob_confirmation(user_input, self._conv.pending_dob)

        if confirmation.user_intent == "wants_to_cancel":
            self._conv.transition(State.USER_ABORTED, trigger="user_cancel")
            return R.ABORTED

        if confirmation.user_intent == "confirmed":
            self._conv.provided_dob = self._conv.pending_dob
            self._conv.pending_dob = None
            self._conv.awaiting_dob_confirmation = False

            if self._conv.has_enough_identity():
                return self._do_verification()
            return self._ask_identity()

        if confirmation.user_intent == "denied":
            # User said the date is wrong — clear and ask again
            self._conv.pending_dob = None
            self._conv.awaiting_dob_confirmation = False
            return R.dob_ambiguous_prompt()

        # Unclear — ask again
        assert self._conv.pending_dob is not None
        return R.dob_confirm_prompt(self._conv.pending_dob)

    def _do_verification(self) -> str:
        self._conv.transition(State.VERIFYING, trigger="identity_collected")
        result = verify_identity(self._conv)

        if result.verified:
            self._conv.transition(State.SHARE_BALANCE, trigger="verified")
            balance = self._conv.account.balance  # type: ignore[union-attr]
            response = R.balance_announcement(balance)
            if balance == Decimal("0"):
                self._conv.transition(State.CONFIRM_AND_CLOSE, trigger="zero_balance")
            else:
                self._conv.transition(State.AWAITING_AMOUNT, trigger="balance_shared")
            return response

        # Verification failed
        self._conv.verification_retries += 1
        if self._conv.verification_retries >= MAX_VERIFICATION_RETRIES:
            self._conv.transition(State.TERMINAL_VERIFICATION_FAILED, trigger="max_verify_retries")
            return R.VERIFICATION_FAILED_TERMINAL

        # Clear all provided identity so user must re-enter
        self._conv.provided_name = None
        self._conv.provided_dob = None
        self._conv.provided_aadhaar4 = None
        self._conv.provided_pincode = None
        self._conv.pending_dob = None
        self._conv.awaiting_dob_confirmation = False

        self._conv.transition(State.AWAITING_IDENTITY, trigger="verify_failed_retry")
        attempts_left = MAX_VERIFICATION_RETRIES - self._conv.verification_retries
        return R.verification_failed_retry(attempts_left)

    def _opportunistic_identity(self, user_input: str) -> str | None:
        """Harvest identity fields from a message whose primary intent was
        something else (account ID on turn 1). Returns the next-step user-facing
        message if any field was captured, else None to fall back to the
        caller's default response."""
        already = {
            "full_name": self._conv.provided_name,
            "dob": self._conv.provided_dob,
            "aadhaar_last4": self._conv.provided_aadhaar4,
            "pincode": self._conv.provided_pincode,
        }
        extraction = extract_identity(user_input, already)

        captured = (
            extraction.full_name is not None
            or extraction.aadhaar_last4 is not None
            or extraction.pincode is not None
            or extraction.dob is not None
            or extraction.dob_ambiguous
        )
        if not captured:
            return None

        if extraction.full_name is not None:
            self._conv.provided_name = extraction.full_name
        if extraction.aadhaar_last4 is not None:
            self._conv.provided_aadhaar4 = extraction.aadhaar_last4
        if extraction.pincode is not None:
            self._conv.provided_pincode = extraction.pincode

        if extraction.dob is not None and not extraction.dob_ambiguous:
            self._conv.pending_dob = extraction.dob
            self._conv.awaiting_dob_confirmation = True
            return R.dob_confirm_prompt(extraction.dob)

        if extraction.dob_ambiguous:
            return R.dob_ambiguous_prompt()

        if self._conv.has_enough_identity():
            return self._do_verification()
        return self._ask_identity()

    def _ask_identity(self) -> str:
        has_name = self._conv.provided_name is not None
        has_secondary = (
            self._conv.provided_dob is not None
            or self._conv.provided_aadhaar4 is not None
            or self._conv.provided_pincode is not None
        )

        if not has_name and not has_secondary:
            return R.ASK_NAME_AND_SECONDARY
        if not has_name:
            return R.ASK_NAME
        if not has_secondary:
            return R.ASK_SECONDARY
        # Should not reach here
        return R.ASK_SECONDARY

    # ── Amount handler ──────────────────────────────────────────────────────

    def _handle_amount(self, user_input: str) -> str:
        account = self._conv.account
        assert account is not None
        balance = account.balance

        extraction = extract_amount(user_input, balance)

        if extraction.user_intent == "wants_to_cancel":
            self._conv.transition(State.USER_ABORTED, trigger="user_cancel")
            return R.ABORTED

        if extraction.wants_full_balance:
            amount = balance
        elif extraction.amount is not None:
            amount = extraction.amount
        else:
            return R.INVALID_AMOUNT

        # Client-side validation before API call
        error = validate_amount(amount, balance)
        if error == "invalid_amount":
            return R.INVALID_AMOUNT
        if error == "insufficient_balance":
            return R.amount_exceeds_balance(balance)

        self._conv.payment_amount = amount
        self._conv.transition(State.AWAITING_CARD, trigger="amount_set")
        return R.ASK_ALL_CARD

    # ── Card handler ────────────────────────────────────────────────────────

    def _handle_card(self, user_input: str) -> str:
        account = self._conv.account
        assert account is not None

        # Build "already collected" context from current card state. Skip
        # fields that were cleared by a prior validation error (stored as
        # empty/zero) so the prompt doesn't claim we have them.
        already: dict = {}
        c = self._conv.card
        if c:
            if c.card_number:
                already["card_number"] = f"****{c.card_number[-4:]}"
            if c.cvv:
                already["cvv"] = "***"
            if c.expiry_month and c.expiry_year:
                already["expiry"] = f"{c.expiry_month:02d}/{c.expiry_year}"
            if c.cardholder_name:
                already["cardholder_name"] = c.cardholder_name

        extraction = extract_card(user_input, already)

        if extraction.user_intent == "wants_to_cancel":
            self._conv.transition(State.USER_ABORTED, trigger="user_cancel")
            return R.ABORTED

        # Merge into existing card state. Treat empty string / zero in the
        # stored partial as "missing" — those values mean a validation error
        # cleared the field on a prior turn.
        def _carry(stored):
            return stored if stored else None

        current = self._conv.card
        card_number = extraction.card_number or _carry(current.card_number if current else None)
        cvv = extraction.cvv or _carry(current.cvv if current else None)
        expiry_month = extraction.expiry_month or _carry(current.expiry_month if current else None)
        expiry_year = extraction.expiry_year or _carry(current.expiry_year if current else None)
        cardholder_name = extraction.cardholder_name or _carry(current.cardholder_name if current else None)

        # Identify missing fields for re-prompting
        missing = []
        if card_number is None:
            missing.append("card_number")
        if cvv is None:
            missing.append("cvv")
        if expiry_month is None or expiry_year is None:
            missing.append("expiry")
        if cardholder_name is None:
            missing.append("cardholder_name")

        if missing:
            # Save partial progress
            if card_number or cvv or expiry_month or expiry_year or cardholder_name:
                self._conv.card = CardDetails(
                    card_number=card_number or "",
                    cvv=cvv or "",
                    expiry_month=expiry_month or 0,
                    expiry_year=expiry_year or 0,
                    cardholder_name=cardholder_name or "",
                )
            return R.ask_card(missing)

        # All fields present — client-side validation
        assert card_number and cvv and expiry_month and expiry_year and cardholder_name

        if not luhn_check(card_number):
            return self._handle_card_validation_error(
                "invalid_card", card_number, cvv, expiry_month, expiry_year, cardholder_name
            )

        if not validate_cvv(cvv, card_number):
            return self._handle_card_validation_error(
                "invalid_cvv", card_number, cvv, expiry_month, expiry_year, cardholder_name
            )

        if not validate_expiry(expiry_month, expiry_year):
            return self._handle_card_validation_error(
                "invalid_expiry", card_number, cvv, expiry_month, expiry_year, cardholder_name
            )

        # All valid — store and process
        self._conv.card = CardDetails(
            card_number=card_number,
            cvv=cvv,
            expiry_month=expiry_month,
            expiry_year=expiry_year,
            cardholder_name=cardholder_name,
        )
        return self._do_payment()

    def _handle_card_validation_error(
        self,
        error_code: str,
        card_number: str,
        cvv: str,
        expiry_month: int,
        expiry_year: int,
        cardholder_name: str,
    ) -> str:
        """Centralized client-side card error handling: increment the payment
        retry counter, persist the non-offending fields so the user only has to
        re-enter what failed, and return the appropriate message. Terminates
        the conversation when retries are exhausted."""
        self._conv.payment_retries += 1
        if self._conv.payment_retries >= MAX_PAYMENT_RETRIES:
            self._conv.transition(State.TERMINAL_PAYMENT_FAILED, trigger="max_payment_retries")
            return R.PAYMENT_FAILED_TERMINAL

        # Save partial card with only the offending field(s) cleared so the
        # merge in _handle_card on the next turn picks up the user's correction.
        self._conv.card = CardDetails(
            card_number="" if error_code == "invalid_card" else card_number,
            cvv="" if error_code == "invalid_cvv" else cvv,
            expiry_month=0 if error_code == "invalid_expiry" else expiry_month,
            expiry_year=0 if error_code == "invalid_expiry" else expiry_year,
            cardholder_name=cardholder_name,
        )

        messages = {
            "invalid_card": R.CARD_LUHN_FAILED,
            "invalid_cvv": R.CARD_CVV_INVALID,
            "invalid_expiry": R.CARD_EXPIRED,
        }
        return messages[error_code]

    def _do_payment(self) -> str:
        conv = self._conv
        assert conv.account is not None
        assert conv.card is not None
        assert conv.payment_amount is not None

        self._conv.transition(State.PROCESSING_PAYMENT, trigger="card_valid")
        try:
            result = process_payment(conv.account.account_id, conv.payment_amount, conv.card)
        except Exception as exc:
            # Defense in depth: process_payment already converts httpx errors
            # to server_error, but a library/JSON bug shouldn't crash the loop
            # or strand us in PROCESSING_PAYMENT. Treat as a retryable
            # server_error so the existing retry path runs.
            logger.exception("process_payment unexpected error: %s", exc)
            result = PaymentResult(success=False, error_code="server_error")

        # Drop card from memory immediately after API call
        conv.clear_card()

        if result.success:
            txn_id = result.transaction_id or "N/A"
            conv.transaction_id = txn_id
            conv.transition(State.CONFIRM_AND_CLOSE, trigger="payment_success")
            return R.payment_success(txn_id, conv.payment_amount)

        error_code = result.error_code or "server_error"

        if error_code in RETRYABLE_PAYMENT_ERRORS:
            conv.payment_retries += 1
            if conv.payment_retries >= MAX_PAYMENT_RETRIES:
                conv.transition(State.TERMINAL_PAYMENT_FAILED, trigger="max_payment_retries")
                return R.PAYMENT_FAILED_TERMINAL
            conv.transition(State.AWAITING_CARD, trigger="payment_retryable_error")
            return R.payment_error_message(error_code)

        # Terminal payment errors (e.g. insufficient_balance post-API)
        conv.transition(State.TERMINAL_PAYMENT_FAILED, trigger=f"payment_terminal_{error_code}")
        return R.payment_error_message(error_code)
