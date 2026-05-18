from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum, auto
from typing import Optional

from event_log import EVENT_STATE_TRANSITION, event_log


class State(Enum):
    INIT = auto()
    AWAITING_ACCOUNT_ID = auto()
    LOOKING_UP_ACCOUNT = auto()
    AWAITING_IDENTITY = auto()
    VERIFYING = auto()
    SHARE_BALANCE = auto()
    AWAITING_AMOUNT = auto()
    AWAITING_CARD = auto()
    PROCESSING_PAYMENT = auto()
    CONFIRM_AND_CLOSE = auto()
    # Terminal states
    TERMINAL_ACCOUNT_NOT_FOUND = auto()
    TERMINAL_VERIFICATION_FAILED = auto()
    TERMINAL_PAYMENT_FAILED = auto()
    TERMINAL_NO_PROGRESS = auto()
    # Repeated unhandled exceptions (LLM down, network blip, schema parse
    # failure, etc.) — closes the session gracefully so the user isn't
    # stuck in an infinite "brief hiccup" loop.
    TERMINAL_TRANSIENT_FAILURES = auto()
    USER_ABORTED = auto()


TERMINAL_STATES = {
    State.TERMINAL_ACCOUNT_NOT_FOUND,
    State.TERMINAL_VERIFICATION_FAILED,
    State.TERMINAL_PAYMENT_FAILED,
    State.TERMINAL_NO_PROGRESS,
    State.TERMINAL_TRANSIENT_FAILURES,
    State.USER_ABORTED,
    State.CONFIRM_AND_CLOSE,
}

# States from which a transient-failure terminal is reachable. Any
# non-terminal state — repeated exceptions can hit anywhere.
_TRANSIENT_RECOVERABLE_STATES = {
    State.AWAITING_ACCOUNT_ID,
    State.LOOKING_UP_ACCOUNT,
    State.AWAITING_IDENTITY,
    State.VERIFYING,
    State.SHARE_BALANCE,
    State.AWAITING_AMOUNT,
    State.AWAITING_CARD,
    State.PROCESSING_PAYMENT,
}


class InvalidTransitionError(RuntimeError):
    """Raised when code attempts a transition outside the FSM allow-list."""

ALLOWED_TRANSITIONS: dict[State, set[State]] = {
    State.INIT: {State.AWAITING_ACCOUNT_ID},
    State.AWAITING_ACCOUNT_ID: {
        State.LOOKING_UP_ACCOUNT,
        State.AWAITING_ACCOUNT_ID,
        State.TERMINAL_ACCOUNT_NOT_FOUND,
        State.USER_ABORTED,
    },
    State.LOOKING_UP_ACCOUNT: {
        State.AWAITING_IDENTITY,
        State.AWAITING_ACCOUNT_ID,
        State.TERMINAL_ACCOUNT_NOT_FOUND,
    },
    State.AWAITING_IDENTITY: {
        State.VERIFYING,
        State.AWAITING_IDENTITY,
        State.USER_ABORTED,
        State.TERMINAL_NO_PROGRESS,
    },
    State.VERIFYING: {
        State.SHARE_BALANCE,
        State.AWAITING_IDENTITY,
        State.TERMINAL_VERIFICATION_FAILED,
    },
    State.SHARE_BALANCE: {State.AWAITING_AMOUNT, State.AWAITING_CARD, State.CONFIRM_AND_CLOSE},
    State.AWAITING_AMOUNT: {
        State.AWAITING_CARD,
        State.AWAITING_AMOUNT,
        State.USER_ABORTED,
        State.TERMINAL_NO_PROGRESS,
    },
    State.AWAITING_CARD: {
        State.PROCESSING_PAYMENT,
        State.AWAITING_CARD,
        State.TERMINAL_PAYMENT_FAILED,
        State.TERMINAL_NO_PROGRESS,
        State.USER_ABORTED,
    },
    State.PROCESSING_PAYMENT: {
        State.CONFIRM_AND_CLOSE,
        State.AWAITING_CARD,
        State.TERMINAL_PAYMENT_FAILED,
    },
    State.CONFIRM_AND_CLOSE: set(),
    State.TERMINAL_ACCOUNT_NOT_FOUND: set(),
    State.TERMINAL_VERIFICATION_FAILED: set(),
    State.TERMINAL_PAYMENT_FAILED: set(),
    State.TERMINAL_NO_PROGRESS: set(),
    State.TERMINAL_TRANSIENT_FAILURES: set(),
    State.USER_ABORTED: set(),
}

# TERMINAL_TRANSIENT_FAILURES is reachable from every non-terminal state
# because the underlying cause (LLM down, network blip, etc.) can fire
# from any handler. Adding it inline above would be 8 duplicate entries;
# patch it in here once.
for _state in _TRANSIENT_RECOVERABLE_STATES:
    ALLOWED_TRANSITIONS[_state].add(State.TERMINAL_TRANSIENT_FAILURES)


@dataclass
class AccountRecord:
    account_id: str
    full_name: str
    dob: date
    aadhaar_last4: str
    pincode: str
    balance: Decimal


@dataclass
class CardDetails:
    card_number: str
    cvv: str
    expiry_month: int
    expiry_year: int
    cardholder_name: str


@dataclass
class TransitionEvent:
    from_state: State
    to_state: State
    trigger: str
    response: str = ""


@dataclass
class ConversationState:
    state: State = State.INIT

    # User-provided fields (collected progressively)
    account_id: Optional[str] = None
    provided_name: Optional[str] = None
    provided_dob: Optional[date] = None
    provided_aadhaar4: Optional[str] = None
    provided_pincode: Optional[str] = None

    # Pending DOB confirmation: the date we extracted but haven't confirmed yet
    pending_dob: Optional[date] = None
    awaiting_dob_confirmation: bool = False

    # Fetched account — set once on successful lookup, never re-fetched
    account: Optional[AccountRecord] = None

    # Payment intent
    payment_amount: Optional[Decimal] = None
    card: Optional[CardDetails] = None

    # User volunteered an amount before verification that exceeded the
    # balance — surface it at balance_announcement time so we don't silently
    # ignore what they said and ask "how much" with no acknowledgment.
    volunteered_amount_over_balance: Optional[Decimal] = None

    # Retry counters — payment is split into two budgets so a user typing
    # wrong card numbers a few times (client- or API-side validation) does
    # NOT consume the budget for genuine API outages, and vice versa. The
    # brief explicitly asks us to "distinguish between user-fixable errors
    # (invalid card) and terminal failures" — sharing one counter conflates
    # the two root causes under a single cap.
    account_lookup_retries: int = 0
    verification_retries: int = 0
    # Bumped on: client-side Luhn / CVV / expiry failure, OR API 422 with
    # invalid_card / invalid_cvv / invalid_expiry (the user can fix these).
    card_validation_retries: int = 0
    # Bumped on: API 5xx exhausted after tenacity retries, or unexpected
    # exception from the payment library (the user can't fix these).
    payment_api_retries: int = 0
    # Consecutive turns where the user produced no useful field advancement
    # in identity / amount / card collection. Bounds refusal loops so the
    # agent doesn't keep re-asking forever when the user never cooperates.
    no_progress_turns: int = 0
    # Consecutive turns ending in a TRANSIENT_ERROR response (LLM down,
    # network blip, schema parse failure). Reset on any successful turn.
    # Bounds the "infinite-hiccup" hole when the LLM is genuinely down.
    consecutive_transient_errors: int = 0

    # Completed transaction
    transaction_id: Optional[str] = None

    # Idempotency key for the current payment attempt. Generated when the
    # user first reaches AWAITING_CARD; reused across tenacity retries and
    # any client-side card validation re-submits so the upstream processor
    # treats them as one logical payment. Regenerated only when the user
    # explicitly starts a new transaction (out of scope here).
    payment_idempotency_key: Optional[str] = None

    # Audit trail (eval reads this)
    transition_log: list[TransitionEvent] = field(default_factory=list)

    def transition(self, new_state: State, trigger: str = "", response: str = "") -> None:
        if new_state not in ALLOWED_TRANSITIONS[self.state]:
            raise InvalidTransitionError(
                f"Invalid transition {self.state} -> {new_state}"
            )
        self.transition_log.append(
            TransitionEvent(self.state, new_state, trigger, response)
        )
        event_log.emit(
            EVENT_STATE_TRANSITION,
            from_state=self.state.name,
            to_state=new_state.name,
            trigger=trigger,
        )
        self.state = new_state

    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def has_enough_identity(self) -> bool:
        """True when we have name + at least one secondary factor."""
        has_name = self.provided_name is not None
        has_secondary = (
            self.provided_dob is not None
            or self.provided_aadhaar4 is not None
            or self.provided_pincode is not None
        )
        return has_name and has_secondary

    def clear_card(self) -> None:
        """Drop card data from memory after API call."""
        self.card = None
