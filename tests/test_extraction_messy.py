"""
Tier 1.5 — Messy extraction accuracy tests.

These call the real LLM extractors with production-style messy inputs and
assert exact field values. They require a live OPENAI_API_KEY and are marked
@pytest.mark.integration so they are skipped in offline CI.

Run manually:
    uv run pytest tests/test_extraction_messy.py -m integration -v
Or via the eval runner:
    uv run python -m eval.run_eval --messy
"""
from __future__ import annotations

import os

import pytest

from eval.messy_cases import MESSY_CASES, run_case

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("OPENAI_API_KEY"),
        reason="requires OPENAI_API_KEY",
    ),
]


@pytest.mark.parametrize("case", MESSY_CASES, ids=[c.label for c in MESSY_CASES])
def test_messy_extraction(case):
    result = run_case(case)
    actual = getattr(result, case.check_field)
    assert actual == case.expected, (
        f"\n[{case.group}] {case.input_text!r}"
        f"\n  expected {case.check_field} = {case.expected!r}"
        f"\n  got      {case.check_field} = {actual!r}"
    )
