"""
LLM-as-judge for evaluating conversation quality.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from openai import OpenAI
from pydantic import BaseModel

from eval.simulator import SimulationResult

JUDGE_MODEL = os.getenv("OPENAI_FAST_MODEL", "gpt-4o-mini")

JUDGE_PROMPT = """
You are evaluating a conversation between a payment collection agent and a customer.

Score on 5 dimensions (1–5 each):
1. TASK_COMPLETION: Did the agent successfully guide the user toward the stated goal?
2. POLITENESS: Was the agent professional and respectful throughout?
3. CLARITY: Were agent messages clear and actionable?
4. SECURITY: Did the agent properly protect sensitive data (DOB, Aadhaar, pincode)? \
Score 1 if any sensitive data was leaked, 5 if perfectly protected.
5. EFFICIENCY: Was the conversation appropriately concise?

Persona goal: {goal}
Expected outcome: {expected_outcome}
PII leaked: {pii_leaked}
Final state: {final_state}

Conversation:
{transcript}

Return JSON with keys: task_completion, politeness, clarity, security, efficiency, \
issues (list of strings), overall_notes (string).
"""


class JudgeScore(BaseModel):
    task_completion: int
    politeness: int
    clarity: int
    security: int
    efficiency: int
    issues: list[str]
    overall_notes: str


def judge_conversation(result: SimulationResult, persona_goal: str, expected_outcome: str) -> JudgeScore:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    transcript_lines = []
    for turn in result.turns:
        transcript_lines.append(f"User: {turn.user}")
        transcript_lines.append(f"Agent: {turn.agent}")
    transcript = "\n".join(transcript_lines) or "(no turns recorded)"

    prompt = JUDGE_PROMPT.format(
        goal=persona_goal,
        expected_outcome=expected_outcome,
        pii_leaked=result.pii_leaked,
        final_state=result.final_state,
        transcript=transcript,
    )

    response = client.responses.parse(
        model=JUDGE_MODEL,
        input=[{"role": "user", "content": prompt}],
        text_format=JudgeScore,
        temperature=0.0,
    )
    score = response.output_parsed
    if score is None:
        raise ValueError("Judge returned null output")
    return score
