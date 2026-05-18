from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from event_log import event_log
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


def _log_extraction(kind: str, user_input: str, result) -> None:
    try:
        output = result.model_dump(mode="json") if hasattr(result, "model_dump") else repr(result)
    except Exception:
        output = repr(result)
    event_log.emit("llm_extract", extractor=kind, input=user_input, output=output)


def extract_account_id(user_input: str) -> AccountIdExtraction:
    prompt = ACCOUNT_ID_EXTRACTION.format(user_input=user_input)
    result = extract_structured(prompt, AccountIdExtraction, model=PRIMARY_MODEL)
    _log_extraction("account_id", user_input, result)
    return result


def extract_identity(user_input: str, already_collected: dict) -> IdentityExtraction:
    collected_str = "\n".join(
        f"  {k}: {v}" for k, v in already_collected.items() if v is not None
    ) or "  (nothing yet)"
    prompt = IDENTITY_EXTRACTION.format(
        user_input=user_input,
        already_collected=collected_str,
    )
    result = extract_structured(prompt, IdentityExtraction, model=PRIMARY_MODEL)
    _log_extraction("identity", user_input, result)
    return result


def extract_dob_confirmation(user_input: str, presented_date: date) -> DobConfirmation:
    date_str = presented_date.strftime("%-d %B %Y")  # e.g. "14 May 1990"
    prompt = DOB_CONFIRMATION.format(
        user_input=user_input,
        presented_date=date_str,
    )
    result = extract_structured(prompt, DobConfirmation, model=PRIMARY_MODEL)
    _log_extraction("dob_confirmation", user_input, result)
    return result


def extract_amount(user_input: str, balance: Decimal) -> AmountExtraction:
    prompt = AMOUNT_EXTRACTION.format(user_input=user_input, balance=f"{balance:,.2f}")
    result = extract_structured(prompt, AmountExtraction, model=PRIMARY_MODEL)
    _log_extraction("amount", user_input, result)
    return result


def extract_card(user_input: str, already_collected: dict) -> CardExtraction:
    collected_str = "\n".join(
        f"  {k}: {v}" for k, v in already_collected.items() if v is not None
    ) or "  (nothing yet)"
    prompt = CARD_EXTRACTION.format(
        user_input=user_input,
        already_collected=collected_str,
    )
    result = extract_structured(prompt, CardExtraction, model=PRIMARY_MODEL)
    _log_extraction("card", user_input, result)
    return result
