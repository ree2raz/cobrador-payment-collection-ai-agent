"""
Unit tests for the simulator's fault-injection wiring.

The Tier-3 persona `api_failure_during_payment` declares
`fault_injection={"payment_api": "server_error"}`. The simulator must
honor this by patching `handlers.process_payment` to return a failing
PaymentResult for the duration of that one conversation, without
affecting any other persona's run.
"""
from __future__ import annotations

import pytest

from eval.personas import PERSONAS, Persona
from eval.simulator import _apply_fault_injection


def _get_persona(name: str) -> Persona:
    for p in PERSONAS:
        if p.name == name:
            return p
    raise AssertionError(f"persona {name!r} not in PERSONAS")


def test_api_failure_persona_is_registered():
    """The api_failure_during_payment persona must be in PERSONAS with
    the expected fault_injection config. Without this, Tier 3 would
    silently run it as a normal cooperative persona."""
    p = _get_persona("api_failure_during_payment")
    assert p.fault_injection == {"payment_api": "server_error"}
    assert p.expected_outcome == "payment_api_failure"


def test_fault_injection_patches_process_payment_in_handlers():
    """Inside the context, handlers.process_payment must return a failing
    PaymentResult — this is what triggers the agent's API-retry path."""
    p = _get_persona("api_failure_during_payment")

    # Outside the context, process_payment is the real function.
    import handlers
    real_fn = handlers.process_payment

    with _apply_fault_injection(p):
        # Inside the context, the function is patched.
        assert handlers.process_payment is not real_fn
        result = handlers.process_payment("ACC1001", 500, None, idempotency_key="x")
        assert result.success is False
        assert result.error_code == "server_error"
        assert result.transaction_id is None

    # After exiting, the original function is restored.
    assert handlers.process_payment is real_fn


def test_no_fault_injection_for_cooperative_persona():
    """A persona without fault_injection must not affect process_payment.
    Otherwise faults would bleed across personas in a Tier-3 run."""
    p = _get_persona("cooperative")
    assert p.fault_injection is None

    import handlers
    real_fn = handlers.process_payment

    with _apply_fault_injection(p):
        # Function unchanged — the context is a no-op.
        assert handlers.process_payment is real_fn
