from __future__ import annotations

import os
import logging
from typing import Any, Type, TypeVar

from openai import OpenAI
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Primary model for extraction + response generation.
# gpt-4o is the frontier model available in this stack.
# Model is configurable via env var for easy swapping.
PRIMARY_MODEL = os.getenv("OPENAI_PRIMARY_MODEL", "gpt-4o")
FAST_MODEL = os.getenv("OPENAI_FAST_MODEL", "gpt-4o-mini")

T = TypeVar("T", bound=BaseModel)

_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


def extract_structured(
    prompt: str,
    schema: Type[T],
    model: str = PRIMARY_MODEL,
    temperature: float = 0.0,
) -> T:
    """
    Call OpenAI with structured output (guaranteed schema compliance).
    Uses Pydantic model as the response schema.
    """
    client = get_client()
    response = client.responses.parse(
        model=model,
        input=[{"role": "user", "content": prompt}],
        text_format=schema,
        temperature=temperature,
    )
    result = response.output_parsed
    if result is None:
        raise ValueError(f"OpenAI returned null output for schema {schema.__name__}")
    return result
