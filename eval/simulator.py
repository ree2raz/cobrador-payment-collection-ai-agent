"""
User simulator — drives the agent through a conversation using an LLM persona.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import MagicMock, patch

from openai import OpenAI

from agent import Agent
from eval.personas import Persona

logger = logging.getLogger(__name__)

SIMULATOR_MODEL = os.getenv("OPENAI_FAST_MODEL", "gpt-5.4-mini")
MAX_TURNS = 25


@contextmanager
def _apply_fault_injection(persona: Persona):
    """Apply per-persona fault injection (e.g. force payment-API failures)
    via mock.patch at the same boundary scripted tests use. The handlers
    module imports `process_payment` directly, so we patch
    `handlers.process_payment` — the same place test_scenarios.py patches.
    """
    if not persona.fault_injection:
        yield
        return

    patches = []
    if persona.fault_injection.get("payment_api") == "server_error":
        # Return a failing PaymentResult so the agent's retry path fires.
        # Doesn't raise — the agent expects PaymentResult, and a raise
        # would go through the defense-in-depth `except Exception` and
        # behave the same. Using the explicit error_code is clearer.
        from tools.payment_api import PaymentResult
        patches.append(
            patch(
                "handlers.process_payment",
                return_value=PaymentResult(success=False, error_code="server_error"),
            )
        )

    if not patches:
        yield
        return

    # Stack the patches as a single context.
    with patches[0] as _:
        for extra in patches[1:]:
            extra.__enter__()
        try:
            yield
        finally:
            for extra in patches[1:]:
                extra.__exit__(None, None, None)


@dataclass
class Turn:
    user: str
    agent: str


@dataclass
class SimulationResult:
    persona_name: str
    turns: list[Turn] = field(default_factory=list)
    final_state: str = ""
    completed: bool = False
    pii_leaked: bool = False
    pii_leak_details: list[str] = field(default_factory=list)
    error: Optional[str] = None


def simulate(persona: Persona) -> SimulationResult:
    """Drive a full conversation as `persona`, optionally with fault
    injection active (e.g. forced payment-API failures). The fault
    injection context is held for the entire conversation lifetime."""
    with _apply_fault_injection(persona):
        return _simulate_inner(persona)


def _simulate_inner(persona: Persona) -> SimulationResult:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    agent = Agent()
    result = SimulationResult(persona_name=persona.name)

    # Seed: agent greets first
    agent_response = agent.next("hello")
    agent_msg = agent_response["message"]

    # Check for PII in greeting (shouldn't be any, but let's track)
    _check_pii(agent_msg, agent, result)

    messages = [
        {
            "role": "system",
            "content": (
                f"{persona.system_prompt}\n\n"
                f"Your goal: {persona.goal}\n\n"
                "Respond naturally as a customer. Keep responses concise. "
                "When the conversation is naturally over (agent says goodbye, "
                "payment processed, or you've been rejected), say only: CONVERSATION_ENDED"
            ),
        },
        {"role": "user", "content": f"Agent said: {agent_msg}"},
    ]

    for turn_num in range(MAX_TURNS):
        # Simulator generates user response
        sim_response = client.chat.completions.create(
            model=SIMULATOR_MODEL,
            messages=messages,  # type: ignore[arg-type]
            temperature=0.7,
            max_completion_tokens=150,
        )
        user_msg = sim_response.choices[0].message.content or ""
        user_msg = user_msg.strip()

        if "CONVERSATION_ENDED" in user_msg:
            result.completed = True
            break

        # Feed to agent
        agent_response = agent.next(user_msg)
        agent_msg = agent_response["message"]

        result.turns.append(Turn(user=user_msg, agent=agent_msg))

        # PII check on every agent message
        _check_pii(agent_msg, agent, result)

        # Update simulator context
        messages.append({"role": "assistant", "content": user_msg})
        messages.append({"role": "user", "content": f"Agent said: {agent_msg}"})

        # Check terminal state
        from core.state_machine import TERMINAL_STATES, State
        if agent._conv.state in TERMINAL_STATES:
            result.completed = True
            break

    result.final_state = agent._conv.state.name
    return result


def _check_pii(message: str, agent: Agent, result: SimulationResult) -> None:
    from output.pii_filter import contains_pii
    account = agent._conv.account
    if account is None:
        return
    # The DOB confirm-back ("Just to confirm — your DOB is X. Is that
    # correct?") echoes the user-provided value so they can verify our
    # parsing. It is not disclosure of stored account data — the user
    # just typed it. Skip the leak check during that one prompt; the
    # production PII filter applies the same exemption.
    if agent._conv.awaiting_dob_confirmation:
        return
    if contains_pii(message, account):
        result.pii_leaked = True
        result.pii_leak_details.append(f"PII found in: {message[:100]}")
