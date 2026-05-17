from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, Optional

from core.normalization import normalize_name
from core.state_machine import AccountRecord, ConversationState


@dataclass
class VerificationResult:
    verified: bool
    name_match: bool
    secondary_match: bool
    which_factor: Optional[Literal["dob", "aadhaar", "pincode"]] = None


def verify_identity(conv: ConversationState) -> VerificationResult:
    """
    Strict verification: Unicode-NFC-normalized exact name match
    AND at least one secondary factor exact match.
    Never fuzzy. Never case-insensitive workaround.
    """
    account = conv.account
    assert account is not None

    name_match = (
        conv.provided_name is not None
        and normalize_name(conv.provided_name) == normalize_name(account.full_name)
    )

    dob_match = conv.provided_dob is not None and conv.provided_dob == account.dob
    aadhaar_match = (
        conv.provided_aadhaar4 is not None
        and conv.provided_aadhaar4 == account.aadhaar_last4
    )
    pincode_match = (
        conv.provided_pincode is not None
        and conv.provided_pincode == account.pincode
    )

    secondary_match = dob_match or aadhaar_match or pincode_match
    which_factor: Optional[str] = None
    if dob_match:
        which_factor = "dob"
    elif aadhaar_match:
        which_factor = "aadhaar"
    elif pincode_match:
        which_factor = "pincode"

    return VerificationResult(
        verified=name_match and secondary_match,
        name_match=name_match,
        secondary_match=secondary_match,
        which_factor=which_factor,  # type: ignore[arg-type]
    )
