from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Optional

from llm.client import FAST_MODEL, PRIMARY_MODEL, extract_structured
from llm.prompts import (
    ACCOUNT_ID_EXTRACTION,
    AMOUNT_EXTRACTION,
    CARD_EXTRACTION,
    DOB_CONFIRMATION,
    IDENTITY_EXTRACTION,
)
from llm.schemas import (
    AccountIdExtraction,
    AmountExtraction,
    CardExtraction,
    DobConfirmation,
    IdentityExtraction,
)

logger = logging.getLogger(__name__)


def extract_account_id(user_input: str) -> AccountIdExtraction:
    prompt = ACCOUNT_ID_EXTRACTION.format(user_input=user_input)
    return extract_structured(prompt, AccountIdExtraction, model=PRIMARY_MODEL)


def extract_identity(user_input: str, already_collected: dict) -> IdentityExtraction:
    collected_str = "\n".join(
        f"  {k}: {v}" for k, v in already_collected.items() if v is not None
    ) or "  (nothing yet)"
    prompt = IDENTITY_EXTRACTION.format(
        user_input=user_input,
        already_collected=collected_str,
    )
    return extract_structured(prompt, IdentityExtraction, model=PRIMARY_MODEL)


def extract_dob_confirmation(user_input: str, presented_date: date) -> DobConfirmation:
    date_str = presented_date.strftime("%-d %B %Y")  # e.g. "14 May 1990"
    prompt = DOB_CONFIRMATION.format(
        user_input=user_input,
        presented_date=date_str,
    )
    return extract_structured(prompt, DobConfirmation, model=PRIMARY_MODEL)


def extract_amount(user_input: str, balance: Decimal) -> AmountExtraction:
    prompt = AMOUNT_EXTRACTION.format(user_input=user_input, balance=f"{balance:,.2f}")
    return extract_structured(prompt, AmountExtraction, model=PRIMARY_MODEL)


def extract_card(user_input: str, already_collected: dict) -> CardExtraction:
    collected_str = "\n".join(
        f"  {k}: {v}" for k, v in already_collected.items() if v is not None
    ) or "  (nothing yet)"
    prompt = CARD_EXTRACTION.format(
        user_input=user_input,
        already_collected=collected_str,
    )
    return extract_structured(prompt, CardExtraction, model=PRIMARY_MODEL)
