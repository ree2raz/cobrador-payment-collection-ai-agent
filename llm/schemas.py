from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class AccountIdExtraction(BaseModel):
    account_id: Optional[str] = Field(
        None,
        description="Normalized account ID like 'ACC1001'. Null if not stated.",
    )
    user_intent: Literal["provided_id", "asking_question", "off_topic", "wants_to_cancel"]


class IdentityExtraction(BaseModel):
    full_name: Optional[str] = Field(
        None,
        description=(
            "User's stated full name as given, preserving original capitalization. "
            "Trim leading/trailing whitespace. Null if not stated."
        ),
    )
    dob: Optional[date] = Field(
        None,
        description=(
            "Date of birth in ISO format (YYYY-MM-DD). Only set if the user provided "
            "a complete, unambiguous date. Null if ambiguous or not provided."
        ),
    )
    dob_ambiguous: bool = Field(
        False,
        description=(
            "True if the user provided a date but the format is ambiguous "
            "(e.g. '01-02-1990' where DD-MM vs MM-DD is unclear)."
        ),
    )
    aadhaar_last4: Optional[str] = Field(
        None,
        description=(
            "Last 4 digits of Aadhaar only. If user provides full 12-digit Aadhaar, "
            "extract only the last 4. Null if not provided."
        ),
    )
    pincode: Optional[str] = Field(
        None,
        description="6-digit Indian pincode, digits only. Null if not provided.",
    )
    user_intent: Literal["providing_info", "asking_question", "wants_to_cancel"]

    @field_validator("full_name", mode="before")
    @classmethod
    def trim_name(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip() or None
        return v

    @field_validator("aadhaar_last4", mode="before")
    @classmethod
    def extract_last4(cls, v: object) -> object:
        if isinstance(v, str):
            digits = "".join(c for c in v if c.isdigit())
            return digits[-4:] if len(digits) >= 4 else None
        return v

    @field_validator("pincode", mode="before")
    @classmethod
    def normalize_pincode(cls, v: object) -> object:
        if isinstance(v, str):
            digits = "".join(c for c in v if c.isdigit())
            return digits if len(digits) == 6 else None
        return v


class DobConfirmation(BaseModel):
    confirmed: bool = Field(
        ...,
        description="True if the user confirmed the date is correct, False if they denied it.",
    )
    user_intent: Literal["confirmed", "denied", "unclear", "wants_to_cancel"]


class AmountExtraction(BaseModel):
    amount: Optional[Decimal] = Field(
        None,
        description=(
            "Payment amount in INR with at most 2 decimal places. "
            "Null if not clearly stated."
        ),
    )
    wants_full_balance: bool = Field(
        False,
        description="True if user said 'clear full amount', 'pay it all', etc.",
    )
    user_intent: Literal["providing_amount", "asking_question", "wants_to_cancel"]

    @field_validator("amount", mode="before")
    @classmethod
    def coerce_amount(cls, v: object) -> object:
        if v is None:
            return v
        try:
            return Decimal(str(v))
        except Exception:
            return None


class CardExtraction(BaseModel):
    card_number: Optional[str] = Field(
        None,
        description="Card number digits only, no spaces. Null if not stated.",
    )
    cvv: Optional[str] = Field(
        None,
        description="CVV digits only. Null if not stated.",
    )
    expiry_month: Optional[int] = Field(None, ge=1, le=12)
    expiry_year: Optional[int] = Field(None, ge=2024, le=2050)
    cardholder_name: Optional[str] = Field(None)
    user_intent: Literal["providing_card", "asking_question", "wants_to_cancel"]

    @field_validator("card_number", mode="before")
    @classmethod
    def strip_card(cls, v: object) -> object:
        if isinstance(v, str):
            digits = "".join(c for c in v if c.isdigit())
            return digits if digits else None
        return v

    @field_validator("cvv", mode="before")
    @classmethod
    def strip_cvv(cls, v: object) -> object:
        if isinstance(v, str):
            digits = "".join(c for c in v if c.isdigit())
            return digits if digits else None
        return v

    @field_validator("expiry_year", mode="before")
    @classmethod
    def normalize_year(cls, v: object) -> object:
        if isinstance(v, int) and v < 100:
            return 2000 + v
        return v
