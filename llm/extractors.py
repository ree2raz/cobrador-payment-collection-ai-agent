from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from event_log import (
    EVENT_LLM_EXTRACT,
    event_log,
    mask_card_number,
    mask_card_substrings,
    mask_cvv_substrings,
)
from llm.client import PRIMARY_MODEL, extract_structured
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
    # Brief: "Do not store or log raw card data beyond what is necessary
    # for the API call." Card data can appear in any extractor's input
    # when the user front-loads it, so we mask both input and output
    # regardless of which extractor fired.
    safe_input = mask_cvv_substrings(mask_card_substrings(user_input))
    if isinstance(output, dict):
        if "card_number" in output and output["card_number"]:
            output["card_number"] = mask_card_number(output["card_number"])
        if "cvv" in output:
            output["cvv"] = "***" if output["cvv"] else output["cvv"]
    event_log.emit(EVENT_LLM_EXTRACT, extractor=kind, input=safe_input, output=output)


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
    # f-string + zero-padded %B avoids the non-portable %-d format
    # (which is glibc-only — crashes on macOS BSD strftime and Windows).
    date_str = f"{presented_date.day} {presented_date.strftime('%B %Y')}"  # e.g. "14 May 1990"
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
