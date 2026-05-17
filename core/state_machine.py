from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum, auto
from typing import Optional


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
    USER_ABORTED = auto()


TERMINAL_STATES = {
    State.TERMINAL_ACCOUNT_NOT_FOUND,
    State.TERMINAL_VERIFICATION_FAILED,
    State.TERMINAL_PAYMENT_FAILED,
    State.USER_ABORTED,
    State.CONFIRM_AND_CLOSE,
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
    State.AWAITING_IDENTITY: {State.VERIFYING, State.AWAITING_IDENTITY, State.USER_ABORTED},
    State.VERIFYING: {
        State.SHARE_BALANCE,
        State.AWAITING_IDENTITY,
        State.TERMINAL_VERIFICATION_FAILED,
    },
    State.SHARE_BALANCE: {State.AWAITING_AMOUNT, State.AWAITING_CARD, State.CONFIRM_AND_CLOSE},
    State.AWAITING_AMOUNT: {State.AWAITING_CARD, State.AWAITING_AMOUNT, State.USER_ABORTED},
    State.AWAITING_CARD: {
        State.PROCESSING_PAYMENT,
        State.AWAITING_CARD,
        State.TERMINAL_PAYMENT_FAILED,
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
    State.USER_ABORTED: set(),
}


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

    # Retry counters
    account_lookup_retries: int = 0
    verification_retries: int = 0
    payment_retries: int = 0

    # Completed transaction
    transaction_id: Optional[str] = None

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
