"""
Per-state handler methods for the payment-collection FSM.

`_CollectionHandlers` is a mixin consumed by `Agent` (`agent.py`). It is
kept separate so `agent.py` reads as "what the agent IS" (class shape,
lifecycle, dispatcher) and this file as "what the agent DOES per state".

Conventions:
- Every handler reads / mutates `self._conv` (the ConversationState).
- Every handler returns a single user-facing string.
- FSM transitions are the only way state changes; transitions go through
  `self._conv.transition()` which enforces the allow-list.
- No direct LLM or HTTP I/O — those live in `llm/` and `tools/`.
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from core.identity_regex import extract_identity_hints
from core.state_machine import CardDetails, State
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
from tools.payment_api import PaymentResult, ServerError, lookup_account, process_payment

logger = logging.getLogger(__name__)

MAX_VERIFICATION_RETRIES = 3
MAX_ACCOUNT_LOOKUP_RETRIES = 3
# Two independent payment budgets — see ConversationState comments for the
# rationale (brief's "user-fixable vs terminal failures" distinction).
MAX_CARD_VALIDATION_RETRIES = 3
MAX_PAYMENT_API_RETRIES = 3

# API 422 error codes that are user-fixable (typos / wrong card data).
# server_error is API-side only.
_USER_FIXABLE_API_ERRORS = {"invalid_card", "invalid_cvv", "invalid_expiry"}
_SERVER_SIDE_API_ERRORS = {"server_error"}
RETRYABLE_PAYMENT_ERRORS = _USER_FIXABLE_API_ERRORS | _SERVER_SIDE_API_ERRORS

# Keywords that suggest the message contains identity information worth
# escalating to the LLM extractor. The deterministic regex extractor (in
# `core.identity_regex`) handles labeled patterns; the LLM handles messier
# forms like lowercase "i am nithin jain". This gate keeps us from calling
# the LLM on pure account-ID messages like "ACC1001".
_IDENTITY_KEYWORDS = re.compile(
    # "i\s+m" catches the common typo "i m" (intended "i am"); the existing
    # alternatives cover "iam", "i am", "im", "i'm".
    r"\b(name|i\s*am|i'?m|i\s+m|this\s+is|dob|date\s+of\s+birth|d\.?o\.?b\.?|born|"
    r"aadhaar|adhaar|pincode|pin\s*code|naam|janam)\b",
    re.IGNORECASE,
)


@dataclass
class _IdentityCapture:
    """Result of `_extract_identity_fields` — fields harvested from one
    message via regex pre-extractor + LLM fallback."""
    name: Optional[str] = None
    dob: Optional[date] = None
    dob_ambiguous: bool = False
    aadhaar_last4: Optional[str] = None
    pincode: Optional[str] = None
    wants_to_cancel: bool = False


class _CollectionHandlers:
    """Per-state handler methods. Mixed into Agent."""

    # ── Shared identity capture (regex + LLM) ───────────────────────────────

    def _extract_identity_fields(
        self,
        user_input: str,
        *,
        already_collected: Optional[dict] = None,
    ) -> _IdentityCapture:
        """Run the deterministic regex pre-extractor, then escalate to the
        LLM if the message looks identity-flavored. Returns the merged
        capture; callers decide what to do with it (stash all fields, or
        also drive DOB confirm-back, or skip DOB entirely, etc.).
        Centralizes the regex+LLM merge that previously lived in two
        near-identical methods.
        """
        hints = extract_identity_hints(user_input)
        cap = _IdentityCapture(
            name=hints.full_name,
            dob=hints.dob,
            dob_ambiguous=hints.dob_ambiguous,
            aadhaar_last4=hints.aadhaar_last4,
            pincode=hints.pincode,
        )
        if _IDENTITY_KEYWORDS.search(user_input):
            already = already_collected or {
                "full_name": None, "dob": None, "aadhaar_last4": None, "pincode": None,
            }
            try:
                extraction = extract_identity(user_input, already)
            except Exception as exc:
                logger.warning("identity extraction failed: %s", exc)
                extraction = None
            if extraction is not None:
                if extraction.user_intent == "wants_to_cancel":
                    cap.wants_to_cancel = True
                    return cap
                cap.name = cap.name or extraction.full_name
                cap.dob = cap.dob or extraction.dob
                cap.dob_ambiguous = cap.dob_ambiguous or (
                    extraction.dob_ambiguous and cap.dob is None
                )
                cap.aadhaar_last4 = cap.aadhaar_last4 or extraction.aadhaar_last4
                cap.pincode = cap.pincode or extraction.pincode
        return cap

    # ── INIT handler (first turn) ───────────────────────────────────────────

    def _handle_init(self, user_input: str) -> str:
        """First turn. Honors brief rule 'never re-ask for info the user
        already provided' — if the opening message volunteers an account
        ID (and maybe identity), process it instead of wasting a turn on
        a pure greeting. A bare greeting falls through without counting
        as a failed account-lookup attempt."""
        self._conv.transition(State.AWAITING_ACCOUNT_ID, trigger="first_turn")
        if not user_input:
            return R.GREETING
        extraction = extract_account_id(user_input)
        if extraction.user_intent == "wants_to_cancel":
            self._conv.transition(State.USER_ABORTED, trigger="user_cancel")
            return R.ABORTED
        if extraction.account_id is None:
            # No account ID — stash any volunteered name / aadhaar /
            # pincode (DOB skipped: no account context yet for confirm-back)
            # so we don't re-ask later. Don't burn a retry on the greeting.
            self._stash_identity_no_dob(user_input)
            return R.GREETING
        self._conv.account_id = extraction.account_id
        response = self._do_lookup()
        return self._maybe_opportunistic(user_input, response)

    # ── Account ID handler ──────────────────────────────────────────────────

    def _handle_account_id(self, user_input: str) -> str:
        # Silent / empty turn — don't waste an LLM call or burn a retry. Just
        # re-prompt. (A real account ID is at least 4 characters: "ACC" + digit.)
        if not user_input or len(user_input.strip()) < 3:
            return R.ASK_ACCOUNT_ID

        extraction = extract_account_id(user_input)

        if extraction.user_intent == "wants_to_cancel":
            self._conv.transition(State.USER_ABORTED, trigger="user_cancel")
            return R.ABORTED

        if extraction.account_id is None:
            # Stash any volunteered identity so we don't re-ask later.
            self._stash_identity_no_dob(user_input)
            # Asking a question shouldn't burn a retry — only an
            # attempted-but-unparseable account ID should.
            if extraction.user_intent == "asking_question":
                return R.ASK_ACCOUNT_ID
            self._conv.account_lookup_retries += 1
            if self._conv.account_lookup_retries >= MAX_ACCOUNT_LOOKUP_RETRIES:
                self._conv.transition(State.TERMINAL_ACCOUNT_NOT_FOUND, trigger="max_id_retries")
                return R.ACCOUNT_LOOKUP_FAILED
            return R.ASK_ACCOUNT_ID

        # Have an account ID — attempt lookup
        self._conv.account_id = extraction.account_id
        response = self._do_lookup()
        return self._maybe_opportunistic(user_input, response)

    def _maybe_opportunistic(self, user_input: str, default_response: str) -> str:
        """After a successful account lookup, rescan the user's message for
        volunteered identity / amount / card details so we don't re-ask for
        info they already provided. Used by both the INIT branch (turn-1
        compound message) and `_handle_account_id` (post-greeting compound
        message — the simulator framework always sends 'hello' as a seed,
        so the compound arrives in AWAITING_ACCOUNT_ID, not INIT).
        """
        if self._conv.state != State.AWAITING_IDENTITY:
            return default_response
        self._opportunistic_payment_details(user_input)
        opportunistic = self._opportunistic_identity(user_input)
        return opportunistic if opportunistic is not None else default_response

    def _do_lookup(self) -> str:
        assert self._conv.account_id is not None
        self._conv.transition(State.LOOKING_UP_ACCOUNT, trigger="start_lookup")

        try:
            result = lookup_account(self._conv.account_id)
        except ServerError:
            # Distinct from account-not-found: tenacity already retried 3x,
            # so the API is genuinely down. Use a different message so the
            # caller knows it's a technical issue, not a security signal.
            self._conv.transition(State.TERMINAL_ACCOUNT_NOT_FOUND, trigger="lookup_server_error")
            return R.LOOKUP_TRANSIENT_ERROR
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

        # Silent/empty turn — re-prompt without burning an LLM call.
        if not user_input:
            return self._ask_identity()

        # Compound capture: user may also volunteer payment amount / card
        # details in the same message. Capture them now so we don't re-ask
        # after verification.
        self._opportunistic_payment_details(user_input)

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
        # Silent/empty turn — just re-prompt confirmation.
        if not user_input:
            return R.dob_confirm_prompt(self._conv.pending_dob)
        # Compound capture: user may say "yes, and my pincode is 400001" or
        # "yes, I want to pay 400". Pick up volunteered fields alongside the
        # confirmation answer.
        self._opportunistic_payment_details(user_input)
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
            if balance == Decimal("0"):
                self._conv.transition(State.CONFIRM_AND_CLOSE, trigger="zero_balance")
                return R.balance_announcement(balance)
            if self._conv.payment_amount is not None:
                response = R.balance_announcement_with_amount(balance, self._conv.payment_amount)
                self._conv.transition(State.AWAITING_CARD, trigger="balance_shared_amount_precollected")
                return f"{response} {self._card_prompt_after_amount(precollected_amount=False)}"
            if self._conv.volunteered_amount_over_balance is not None:
                attempted = self._conv.volunteered_amount_over_balance
                self._conv.volunteered_amount_over_balance = None
                self._conv.transition(State.AWAITING_AMOUNT, trigger="balance_shared_over_balance")
                return R.balance_announcement_over_amount(balance, attempted)
            self._conv.transition(State.AWAITING_AMOUNT, trigger="balance_shared")
            return R.balance_announcement(balance)

        # Verification failed
        self._conv.verification_retries += 1
        if self._conv.verification_retries >= MAX_VERIFICATION_RETRIES:
            self._conv.transition(State.TERMINAL_VERIFICATION_FAILED, trigger="max_verify_retries")
            return R.VERIFICATION_FAILED_TERMINAL

        # Keep the fields the user already provided intact. Wiping everything
        # would force the user to re-confirm DOB / re-state secondary factor
        # even when only one field (e.g. name) was the actual mismatch — the
        # next-turn extractor overwrites whichever field they re-state. The
        # verification_retries counter still bounds abuse.
        self._conv.pending_dob = None
        self._conv.awaiting_dob_confirmation = False

        self._conv.transition(State.AWAITING_IDENTITY, trigger="verify_failed_retry")
        attempts_left = MAX_VERIFICATION_RETRIES - self._conv.verification_retries
        return R.verification_failed_retry(attempts_left)

    def _opportunistic_identity(self, user_input: str) -> Optional[str]:
        """Harvest identity fields from a message whose primary intent was
        something else (account ID on turn 1). Returns the next-step
        user-facing message if any field was captured, else None to fall
        back to the caller's default response."""
        already = {
            "full_name": self._conv.provided_name,
            "dob": self._conv.provided_dob,
            "aadhaar_last4": self._conv.provided_aadhaar4,
            "pincode": self._conv.provided_pincode,
        }
        cap = self._extract_identity_fields(user_input, already_collected=already)

        if cap.wants_to_cancel:
            self._conv.transition(State.USER_ABORTED, trigger="user_cancel")
            return R.ABORTED

        captured = (
            any((cap.name, cap.dob, cap.aadhaar_last4, cap.pincode))
            or cap.dob_ambiguous
        )
        if not captured:
            return None

        if cap.name is not None:
            self._conv.provided_name = cap.name
        if cap.aadhaar_last4 is not None:
            self._conv.provided_aadhaar4 = cap.aadhaar_last4
        if cap.pincode is not None:
            self._conv.provided_pincode = cap.pincode

        if cap.dob is not None and not cap.dob_ambiguous:
            self._conv.pending_dob = cap.dob
            self._conv.awaiting_dob_confirmation = True
            return R.dob_confirm_prompt(cap.dob)

        if cap.dob_ambiguous:
            return R.dob_ambiguous_prompt()

        if self._conv.has_enough_identity():
            return self._do_verification()
        return self._ask_identity()

    def _stash_identity_no_dob(self, user_input: str) -> None:
        """Stash name/aadhaar/pincode volunteered before we have an account.
        DOB is intentionally skipped — we can't run the confirm-back flow
        without an account, and the user will re-state it naturally when
        asked for identity. Only fills empty fields; never overwrites."""
        cap = self._extract_identity_fields(user_input)
        if cap.name and self._conv.provided_name is None:
            self._conv.provided_name = cap.name
        if cap.aadhaar_last4 and self._conv.provided_aadhaar4 is None:
            self._conv.provided_aadhaar4 = cap.aadhaar_last4
        if cap.pincode and self._conv.provided_pincode is None:
            self._conv.provided_pincode = cap.pincode

    def _opportunistic_payment_details(self, user_input: str) -> None:
        """Capture volunteered payment details without advancing payment flow.

        This preserves context from users who front-load "pay 500 on this card"
        while still enforcing the mandatory order: lookup -> verification ->
        balance announcement -> payment.
        """
        account = self._conv.account
        if account is None:
            return

        if self._looks_like_amount_input(user_input):
            amount_extraction = extract_amount(user_input, account.balance)
            if amount_extraction.user_intent != "wants_to_cancel":
                if amount_extraction.wants_full_balance:
                    amount = account.balance
                else:
                    amount = amount_extraction.amount
                if amount is not None:
                    err = validate_amount(amount, account.balance)
                    if err is None:
                        self._conv.payment_amount = amount
                    elif err == "insufficient_balance":
                        # Stash so we can acknowledge at balance-announcement
                        # time instead of silently asking "how much" again.
                        self._conv.volunteered_amount_over_balance = amount

        if not self._looks_like_card_input(user_input):
            return
        card_extraction = extract_card(user_input, {})
        if card_extraction.user_intent == "wants_to_cancel":
            return
        if not any(
            (
                card_extraction.card_number,
                card_extraction.cvv,
                card_extraction.expiry_month,
                card_extraction.expiry_year,
                card_extraction.cardholder_name,
            )
        ):
            return
        self._conv.card = CardDetails(
            card_number=card_extraction.card_number or "",
            cvv=card_extraction.cvv or "",
            expiry_month=card_extraction.expiry_month or 0,
            expiry_year=card_extraction.expiry_year or 0,
            cardholder_name=card_extraction.cardholder_name or "",
        )

    @staticmethod
    def _looks_like_card_input(user_input: str) -> bool:
        # Require an explicit card-related keyword. A pure digit-count rule
        # (e.g. >=13) fires spuriously when identity + amount digits stack up
        # — DOB (8) + account ID digits + Aadhaar (4) + pincode (6) easily
        # exceeds 13 with no card present.
        lowered = user_input.lower()
        return any(
            token in lowered for token in ("card", "cvv", "expiry", "expires", "exp ")
        )

    @staticmethod
    def _looks_like_amount_input(user_input: str) -> bool:
        lowered = user_input.lower()
        return any(
            token in lowered
            for token in ("pay", "rupee", "₹", "rs", "amount", "full balance", "clear it", "pay it all")
        )

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
        return R.ASK_SECONDARY

    # ── Amount handler ──────────────────────────────────────────────────────

    def _handle_amount(self, user_input: str) -> str:
        account = self._conv.account
        assert account is not None
        balance = account.balance

        # Silent/empty turn — re-prompt without burning an LLM call.
        if not user_input:
            return R.ask_amount(balance)

        # Compound capture: user may say "pay 500 with my card 4532..." —
        # grab the card details now so we don't re-ask after the amount step.
        self._opportunistic_payment_details(user_input)

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
        # Acknowledge what the user just said before pivoting to card
        # collection — otherwise the response reads as a non-sequitur
        # ("Please provide your card details…") with no indication we
        # heard them.
        return R.acknowledge_amount(amount) + self._card_prompt_after_amount()

    def _card_prompt_after_amount(self, precollected_amount: bool = False) -> str:
        prefix = (
            f"I have the payment amount as ₹{self._conv.payment_amount:,.2f}. "
            if precollected_amount and self._conv.payment_amount is not None
            else ""
        )
        card = self._conv.card
        if card is None:
            return prefix + R.ASK_ALL_CARD
        missing = []
        if not card.card_number:
            missing.append("card_number")
        if not card.cvv:
            missing.append("cvv")
        if not card.expiry_month or not card.expiry_year:
            missing.append("expiry")
        if not card.cardholder_name:
            missing.append("cardholder_name")
        if missing:
            return prefix + R.ask_card(missing)
        return (
            prefix
            + "I also have the card details you already provided. "
            "Please confirm I should use those details, or re-enter them if anything has changed."
        )

    # ── Card handler ────────────────────────────────────────────────────────

    def _handle_card(self, user_input: str) -> str:
        account = self._conv.account
        assert account is not None

        # Silent/empty turn — re-prompt for whatever's missing without
        # burning an LLM call.
        if not user_input:
            return self._card_prompt_after_amount()

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

        # Merge into existing card state. Empty string / zero in the stored
        # partial means the field was cleared by a prior validation error.
        current = self._conv.card
        card_number = extraction.card_number or (current.card_number if current else None) or None
        cvv = extraction.cvv or (current.cvv if current else None) or None
        expiry_month = extraction.expiry_month or (current.expiry_month if current else None) or None
        expiry_year = extraction.expiry_year or (current.expiry_year if current else None) or None
        cardholder_name = extraction.cardholder_name or (current.cardholder_name if current else None) or None

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
        """Centralized client-side card error handling: increment the
        card-validation retry counter (separate from API-side retries),
        persist the non-offending fields so the user only has to re-enter
        what failed, and return the appropriate message. Terminates when
        retries are exhausted."""
        self._conv.card_validation_retries += 1
        if self._conv.card_validation_retries >= MAX_CARD_VALIDATION_RETRIES:
            self._conv.transition(State.TERMINAL_PAYMENT_FAILED, trigger="max_card_validation_retries")
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
        # One idempotency key per logical charge attempt. tenacity's
        # internal retries within this single process_payment call share
        # the key (preventing duplicate charges on network blips). If the
        # user later submits different card data, _do_payment is invoked
        # again and gets a fresh key — those are genuinely different
        # payment intents.
        conv.payment_idempotency_key = uuid.uuid4().hex
        try:
            result = process_payment(
                conv.account.account_id,
                conv.payment_amount,
                conv.card,
                idempotency_key=conv.payment_idempotency_key,
            )
        except Exception as exc:
            # Defense in depth: process_payment already converts httpx errors
            # to server_error, but a library/JSON bug shouldn't crash the loop
            # or strand us in PROCESSING_PAYMENT. Treat as a retryable
            # server_error so the existing retry path runs.
            logger.exception("process_payment unexpected error: %s", exc)
            result = PaymentResult(success=False, error_code="server_error")

        # Snapshot the submitted card so we can selectively clear fields
        # on user-fixable API errors. Then defer the global clear_card()
        # to the specific branches below.
        submitted_card = conv.card

        if result.success:
            conv.clear_card()  # brief: drop after API success
            txn_id = result.transaction_id or "N/A"
            conv.transaction_id = txn_id
            conv.transition(State.CONFIRM_AND_CLOSE, trigger="payment_success")
            return R.payment_success(txn_id, conv.payment_amount)

        error_code = result.error_code or "server_error"

        if error_code in RETRYABLE_PAYMENT_ERRORS:
            # API 422 with invalid_card/invalid_cvv/invalid_expiry → the API
            # rejected the user's card data → counts against the card-
            # validation budget (a typo, just caught upstream from us).
            # API 5xx exhausted / server_error → counts against the API budget.
            if error_code in _USER_FIXABLE_API_ERRORS:
                # Mirror _handle_card_validation_error: clear ONLY the
                # offending field so the user re-enters one thing, not
                # the entire card. Previously clear_card() ran first,
                # wiping all four fields and forcing full re-entry —
                # inconsistent with the client-side path.
                conv.card = CardDetails(
                    card_number="" if error_code == "invalid_card" else submitted_card.card_number,
                    cvv="" if error_code == "invalid_cvv" else submitted_card.cvv,
                    expiry_month=0 if error_code == "invalid_expiry" else submitted_card.expiry_month,
                    expiry_year=0 if error_code == "invalid_expiry" else submitted_card.expiry_year,
                    cardholder_name=submitted_card.cardholder_name,
                )
                conv.card_validation_retries += 1
                limit = MAX_CARD_VALIDATION_RETRIES
                counter = conv.card_validation_retries
                trigger_max = "max_card_validation_retries"
            else:
                # server_error: ambiguous what the API saw. Safest to
                # drop all card data and let the user re-enter.
                conv.clear_card()
                conv.payment_api_retries += 1
                limit = MAX_PAYMENT_API_RETRIES
                counter = conv.payment_api_retries
                trigger_max = "max_payment_api_retries"
            if counter >= limit:
                # Exhausted budget — drop card data on the way out.
                conv.clear_card()
                conv.transition(State.TERMINAL_PAYMENT_FAILED, trigger=trigger_max)
                return R.PAYMENT_FAILED_TERMINAL
            conv.transition(State.AWAITING_CARD, trigger="payment_retryable_error")
            return R.payment_error_message(error_code)

        # Terminal payment errors (e.g. insufficient_balance post-API).
        # Drop card on the way out — no more attempts in this session.
        conv.clear_card()
        conv.transition(State.TERMINAL_PAYMENT_FAILED, trigger=f"payment_terminal_{error_code}")
        return R.payment_error_message(error_code)
