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

    # Internal-only debug signal: did the name fail only because of case?
    # Never surfaced to the user (would weaken strict-matching guarantee),
    # but tells us when the LLM extractor failed to title-case input.
    name_case_only_mismatch = (
        not name_match
        and conv.provided_name is not None
        and normalize_name(conv.provided_name).casefold()
        == normalize_name(account.full_name).casefold()
    )

    from event_log import EVENT_VERIFICATION, event_log
    event_log.emit(
        EVENT_VERIFICATION,
        verified=(name_match and secondary_match),
        name_match=name_match,
        name_provided=conv.provided_name,
        name_account=account.full_name,
        name_case_only_mismatch=name_case_only_mismatch,
        dob_match=dob_match,
        dob_provided=conv.provided_dob,
        dob_account=account.dob,
        aadhaar_match=aadhaar_match,
        aadhaar_provided=conv.provided_aadhaar4,
        aadhaar_account=account.aadhaar_last4,
        pincode_match=pincode_match,
        pincode_provided=conv.provided_pincode,
        pincode_account=account.pincode,
        which_factor=which_factor,
    )

    return VerificationResult(
        verified=name_match and secondary_match,
        name_match=name_match,
        secondary_match=secondary_match,
        which_factor=which_factor,  # type: ignore[arg-type]
    )
