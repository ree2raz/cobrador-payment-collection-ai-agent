import pytest
from core.state_machine import (
    ALLOWED_TRANSITIONS,
    InvalidTransitionError,
    TERMINAL_STATES,
    ConversationState,
    State,
)


class TestAllowedTransitions:
    def test_init_to_awaiting(self):
        assert State.AWAITING_ACCOUNT_ID in ALLOWED_TRANSITIONS[State.INIT]

    def test_terminal_states_have_no_outgoing(self):
        for s in (
            State.TERMINAL_ACCOUNT_NOT_FOUND,
            State.TERMINAL_VERIFICATION_FAILED,
            State.TERMINAL_PAYMENT_FAILED,
            State.USER_ABORTED,
            State.CONFIRM_AND_CLOSE,
        ):
            assert ALLOWED_TRANSITIONS[s] == set()

    def test_verify_can_succeed_or_fail(self):
        transitions = ALLOWED_TRANSITIONS[State.VERIFYING]
        assert State.SHARE_BALANCE in transitions
        assert State.AWAITING_IDENTITY in transitions
        assert State.TERMINAL_VERIFICATION_FAILED in transitions


class TestConversationStateTransition:
    def test_valid_transition(self):
        conv = ConversationState()
        conv.transition(State.AWAITING_ACCOUNT_ID, trigger="test")
        assert conv.state == State.AWAITING_ACCOUNT_ID

    def test_invalid_transition_raises(self):
        conv = ConversationState()
        with pytest.raises(InvalidTransitionError):
            conv.transition(State.CONFIRM_AND_CLOSE)

    def test_transition_logged(self):
        conv = ConversationState()
        conv.transition(State.AWAITING_ACCOUNT_ID, trigger="greeting")
        assert len(conv.transition_log) == 1
        assert conv.transition_log[0].trigger == "greeting"

    def test_is_terminal(self):
        conv = ConversationState(state=State.TERMINAL_ACCOUNT_NOT_FOUND)
        assert conv.is_terminal() is True

    def test_not_terminal(self):
        conv = ConversationState(state=State.AWAITING_IDENTITY)
        assert conv.is_terminal() is False


class TestHasEnoughIdentity:
    def test_no_fields(self):
        conv = ConversationState()
        assert conv.has_enough_identity() is False

    def test_name_only(self):
        conv = ConversationState()
        conv.provided_name = "Nithin Jain"
        assert conv.has_enough_identity() is False

    def test_name_and_dob(self):
        from datetime import date
        conv = ConversationState()
        conv.provided_name = "Nithin Jain"
        conv.provided_dob = date(1990, 5, 14)
        assert conv.has_enough_identity() is True

    def test_name_and_aadhaar(self):
        conv = ConversationState()
        conv.provided_name = "Nithin Jain"
        conv.provided_aadhaar4 = "4321"
        assert conv.has_enough_identity() is True

    def test_name_and_pincode(self):
        conv = ConversationState()
        conv.provided_name = "Nithin Jain"
        conv.provided_pincode = "400001"
        assert conv.has_enough_identity() is True
