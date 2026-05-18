"""
Payment Collection Agent — Cobrador

Architecture: Deterministic FSM owns all flow control. LLM is used only for
structured extraction of messy natural language into typed fields. Templated
responses are used for 90% of agent output (deterministic, testable, PII-safe).

This file holds the Agent class shape:
- public `next()` entry point with PII redaction post-processor
- top-level dispatcher (`_process`)
- lifecycle helpers (progress tracking, no-progress termination)

Per-state handler methods live in `handlers.py`; we inherit them as a
mixin so this file reads as "what the agent IS" and the other reads as
"what the agent DOES per state". Tests patch handler-level symbols
through `agent.<name>` (e.g. `agent.lookup_account`) — those names are
re-exported below for backward compatibility.
"""
from __future__ import annotations

import logging

from event_log import (
    EVENT_CONVERSATION_START,
    EVENT_TURN_END,
    EVENT_TURN_ERROR,
    EVENT_TURN_START,
    event_log,
    mask_card_substrings,
    mask_cvv_substrings,
)
from core.state_machine import (
    ConversationState,
    InvalidTransitionError,
    State,
    TERMINAL_STATES,
)
from handlers import _CollectionHandlers
from output import responses as R
from output.pii_filter import redact_pii

logger = logging.getLogger(__name__)

# Consecutive turns with zero useful progress before we close gracefully.
# Five is generous: a cooperative user always makes progress every turn;
# this only kicks in for refusal loops / prompt-injection attempts where
# the user is never going to provide what we need.
MAX_NO_PROGRESS_TURNS = 5

# Consecutive turns ending in a generic TRANSIENT_ERROR before we close
# the session. Closes the "LLM-down infinite-hiccup" hole.
MAX_CONSECUTIVE_TRANSIENT_ERRORS = 3

# States where the no-progress counter applies (the three collection loops)
_NO_PROGRESS_STATES = {
    "AWAITING_IDENTITY",
    "AWAITING_AMOUNT",
    "AWAITING_CARD",
}


class Agent(_CollectionHandlers):
    def __init__(self) -> None:
        self._conv = ConversationState()
        event_log.new_conversation()
        event_log.emit(EVENT_CONVERSATION_START)

    def next(self, user_input: str) -> dict:
        """Process one turn. Returns {"message": str}.

        Wraps `_process` in an exception boundary: upstream/transient
        exceptions (OpenAI error, network blip, schema-parse failure, etc.)
        are logged and rendered as a generic retry message. Internal FSM
        invariant failures are re-raised so tests and operators see real
        code bugs."""
        user_input = user_input.strip()
        progress_snapshot_before = self._progress_snapshot()
        event_log.emit(
            EVENT_TURN_START,
            state=self._conv.state.name,
            user_input=mask_cvv_substrings(mask_card_substrings(user_input)),
            provided_name=self._conv.provided_name,
            provided_dob=self._conv.provided_dob,
            provided_aadhaar4=self._conv.provided_aadhaar4,
            provided_pincode=self._conv.provided_pincode,
            payment_amount=self._conv.payment_amount,
            volunteered_over=self._conv.volunteered_amount_over_balance,
        )
        hit_transient_error = False
        try:
            response = self._process(user_input)
        except (InvalidTransitionError, AssertionError):
            logger.exception("Internal state invariant failed (state=%s)", self._conv.state)
            event_log.emit(EVENT_TURN_ERROR, state=self._conv.state.name, kind="invariant")
            raise
        except Exception as exc:
            logger.exception(
                "Unhandled error in turn (state=%s): %s", self._conv.state, exc
            )
            event_log.emit(EVENT_TURN_ERROR, state=self._conv.state.name, error=repr(exc))
            response = R.TRANSIENT_ERROR
            hit_transient_error = True

        # Bound the "LLM is genuinely down" hole: if multiple consecutive
        # turns hit the exception boundary, close gracefully instead of
        # asking the user to repeat indefinitely. Reset on any successful
        # turn (where the handler returned normally, even with a
        # state-unchanged re-prompt — that still counts as the system
        # functioning).
        if hit_transient_error:
            self._conv.consecutive_transient_errors += 1
            if (
                self._conv.consecutive_transient_errors >= MAX_CONSECUTIVE_TRANSIENT_ERRORS
                and not self._conv.is_terminal()
            ):
                self._conv.transition(
                    State.TERMINAL_TRANSIENT_FAILURES, trigger="max_consecutive_transient_errors"
                )
                response = R.TRANSIENT_FAILURES_TERMINAL
        else:
            self._conv.consecutive_transient_errors = 0
        # Final PII redaction layer — defense in depth. Allow DOB readback
        # only while we're prompting the customer to confirm the date we
        # parsed; otherwise they'd see "[REDACTED]" and can't confirm.
        response = redact_pii(
            response,
            self._conv.account,
            allow_dob_readback=self._conv.awaiting_dob_confirmation,
        )

        # No-progress check: if we're still in a collection state and
        # nothing useful changed in the snapshot, bump the counter and
        # close gracefully at the threshold. Cooperative users always
        # advance at least one field per turn.
        if (
            self._conv.state.name in _NO_PROGRESS_STATES
            and progress_snapshot_before == self._progress_snapshot()
        ):
            self._conv.no_progress_turns += 1
            if self._conv.no_progress_turns >= MAX_NO_PROGRESS_TURNS:
                response = self._terminate_no_progress()
        else:
            self._conv.no_progress_turns = 0

        logger.debug("state=%s response=%r", self._conv.state, response[:80])
        event_log.emit(EVENT_TURN_END, state=self._conv.state.name, response=response)
        return {"message": response}

    # ── Lifecycle helpers ───────────────────────────────────────────────────

    def _progress_snapshot(self) -> tuple:
        """Snapshot of fields that count as 'progress'. Two equal snapshots
        across a turn means the user produced nothing useful."""
        c = self._conv
        card = c.card
        card_tuple = (
            (card.card_number, card.cvv, card.expiry_month, card.expiry_year, card.cardholder_name)
            if card else None
        )
        return (
            c.state,
            c.account_id,
            c.provided_name,
            c.provided_dob,
            c.provided_aadhaar4,
            c.provided_pincode,
            c.pending_dob,
            c.awaiting_dob_confirmation,
            c.payment_amount,
            c.volunteered_amount_over_balance,
            card_tuple,
            c.verification_retries,
            c.account_lookup_retries,
            c.card_validation_retries,
            c.payment_api_retries,
        )

    def _terminate_no_progress(self) -> str:
        state_name = self._conv.state.name
        if state_name == "AWAITING_IDENTITY":
            msg = R.NO_PROGRESS_IDENTITY
        elif state_name == "AWAITING_AMOUNT":
            msg = R.NO_PROGRESS_AMOUNT
        else:
            msg = R.NO_PROGRESS_CARD
        self._conv.transition(State.TERMINAL_NO_PROGRESS, trigger="no_progress")
        return msg

    # ── Main dispatch ───────────────────────────────────────────────────────

    def _process(self, user_input: str) -> str:
        state = self._conv.state

        # Absorb all input in terminal states
        if state in TERMINAL_STATES:
            return R.CLOSING if state == State.CONFIRM_AND_CLOSE else R.ABORTED

        if state == State.INIT:
            return self._handle_init(user_input)

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
