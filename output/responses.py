"""
Templated user-facing messages. 90% of agent output comes from here — deterministic,
testable, and guaranteed PII-free (we control every byte).
LLM-generated messages are only used for the balance announcement and payment confirmation.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

# ── Greeting & account collection ──────────────────────────────────────────

GREETING = (
    "Hello! I'm here to help you with your account payment. "
    "To get started, could you please share your account ID?"
)

ASK_ACCOUNT_ID = "Could you please share your account ID? It should look like 'ACC' followed by numbers."

ACCOUNT_NOT_FOUND = (
    "I wasn't able to find an account with that ID. "
    "Please double-check and share your account ID again."
)

ACCOUNT_LOOKUP_FAILED = (
    "I'm unable to complete verification at the moment, so I cannot discuss the account "
    "on this call. Please contact us through the official number on your notice, "
    "or we can try again later. Thank you for your patience."
)

# Used when the lookup endpoint itself is unreachable (5xx / network) after
# retries — distinct from account-not-found so we can tell the user it's a
# technical issue, not a security/enumeration outcome. Terminal because
# tenacity already retried 3x with backoff before raising.
LOOKUP_TRANSIENT_ERROR = (
    "I'm having trouble reaching our account system right now — this looks like "
    "a temporary technical issue on our side. Please try again in a few minutes, "
    "or call back using the number on your notice. Thank you for your patience."
)

# ── Identity collection ─────────────────────────────────────────────────────

ASK_NAME = "Thank you. Could you please confirm your full name as it appears on your account?"

ASK_SECONDARY = (
    "Thank you. To complete verification, could you please provide one of the following: "
    "your date of birth, the last 4 digits of your Aadhaar, or your pincode?"
)

ASK_NAME_AND_SECONDARY = (
    "To verify your identity, I'll need your full name and one of the following: "
    "your date of birth, the last 4 digits of your Aadhaar, or your pincode."
)

def dob_confirm_prompt(dob: date) -> str:
    day = dob.day
    suffix = "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return (
        f"Just to confirm — your date of birth is {day}{suffix} {dob.strftime('%B %Y')}. "
        "Is that correct?"
    )

def dob_ambiguous_prompt() -> str:
    return (
        "I wasn't able to determine your date of birth from that. "
        "Could you share it in a clear format, like '14th May 1990' or 'May 14, 1990'?"
    )

# ── Verification outcomes ───────────────────────────────────────────────────

def verification_failed_retry(attempts_left: int) -> str:
    # We can't tell the user which field was wrong (would expose account data),
    # so we suggest trying a different secondary factor — a typo in one field
    # is the most common failure mode for cooperative users.
    suggestion = (
        "Please re-check your full name. If you don't recall your date of birth "
        "exactly, you can try the last 4 digits of your Aadhaar or your pincode instead."
    )
    if attempts_left == 1:
        return (
            "The details you provided don't match our records. "
            f"This is your last attempt. {suggestion}"
        )
    return (
        f"The details you provided don't match our records — "
        f"you have {attempts_left} attempt(s) remaining. {suggestion}"
    )

VERIFICATION_FAILED_TERMINAL = (
    "I'm sorry, but I wasn't able to verify your identity after multiple attempts. "
    "For security reasons, I'm unable to proceed with this call. "
    "Please contact us through the official number on your notice. Thank you."
)

# ── Balance announcement ────────────────────────────────────────────────────

def balance_announcement(balance: Decimal) -> str:
    formatted = f"₹{balance:,.2f}"
    if balance == 0:
        return (
            f"Your identity has been verified. Your outstanding balance is {formatted}. "
            "There is nothing to pay at this time. Thank you, and have a great day!"
        )
    return (
        f"Your identity has been verified. Your outstanding balance is {formatted}. "
        "How much would you like to pay today? You can pay the full amount or a partial amount."
    )

def balance_announcement_with_amount(balance: Decimal, amount: Decimal) -> str:
    """Used when the user pre-volunteered the payment amount in an earlier
    turn — no need to ask 'how much' again."""
    return (
        f"Your identity has been verified. Your outstanding balance is ₹{balance:,.2f}, "
        f"and you'd like to pay ₹{amount:,.2f}."
    )

def balance_announcement_over_amount(balance: Decimal, attempted: Decimal) -> str:
    """Used when the user volunteered a payment amount before verification
    that exceeds the balance. Acknowledges the attempted amount so the user
    doesn't feel ignored, then re-prompts within the valid range."""
    return (
        f"Your identity has been verified. You mentioned ₹{attempted:,.2f} earlier, "
        f"but your outstanding balance is only ₹{balance:,.2f} — that's the maximum "
        "you can pay today. How much would you like to pay?"
    )

# ── Amount collection ───────────────────────────────────────────────────────

def amount_exceeds_balance(balance: Decimal) -> str:
    return (
        f"That amount exceeds your outstanding balance of ₹{balance:,.2f}. "
        f"Please enter an amount up to ₹{balance:,.2f}."
    )

INVALID_AMOUNT = (
    "I couldn't understand that amount. Please enter a valid amount in rupees, "
    "for example: '500' or '1250.75'."
)

def ask_amount(balance: Decimal) -> str:
    return (
        f"Your outstanding balance is ₹{balance:,.2f}. "
        "How much would you like to pay today? You can pay the full amount or a partial amount."
    )

# ── Card collection ─────────────────────────────────────────────────────────

def ask_card(missing_fields: list[str]) -> str:
    if not missing_fields:
        return "Please provide your card details."
    field_names = {
        "card_number": "card number",
        "cvv": "CVV",
        "expiry": "expiry date (month and year)",
        "cardholder_name": "cardholder name",
    }
    needed = [field_names.get(f, f) for f in missing_fields]
    if len(needed) == 1:
        return f"Could you please provide your {needed[0]}?"
    return f"Could you please provide your {', '.join(needed[:-1])} and {needed[-1]}?"

ASK_ALL_CARD = (
    "Please provide your card details: card number, expiry date, CVV, and cardholder name."
)

# ── Card/payment errors ─────────────────────────────────────────────────────

CARD_ERROR_MESSAGES: dict[str, str] = {
    "invalid_card": (
        "The card number appears to be invalid. "
        "Please double-check and re-enter your card number."
    ),
    "invalid_cvv": (
        "The CVV you provided doesn't appear to be valid. "
        "Please re-enter your CVV (3 digits on the back of the card, or 4 digits for Amex)."
    ),
    "invalid_expiry": (
        "The card expiry date is invalid or the card has expired. "
        "Please re-enter your expiry date, or use a different card."
    ),
    "insufficient_balance": (
        "The payment amount exceeds the account balance. "
        "Please try a lower amount."
    ),
    "invalid_amount": (
        "The payment amount is invalid. Please ensure it is greater than zero "
        "and has no more than 2 decimal places."
    ),
    "server_error": (
        "We're experiencing a technical issue processing your payment right now. "
        "Please try again in a moment."
    ),
}

CARD_LUHN_FAILED = (
    "The card number doesn't appear to be valid. "
    "Please re-enter your 16-digit card number carefully."
)

CARD_EXPIRED = (
    "This card appears to have expired. "
    "Please use a valid card or re-check the expiry date."
)

CARD_CVV_INVALID = (
    "The CVV you entered is not the right length. "
    "Please provide the 3-digit CVV on the back of your card (or 4 digits for Amex)."
)

def payment_error_message(error_code: str) -> str:
    return CARD_ERROR_MESSAGES.get(error_code, CARD_ERROR_MESSAGES["server_error"])

PAYMENT_FAILED_TERMINAL = (
    "I'm sorry, I was unable to process your payment after multiple attempts. "
    "Please contact us through the official number on your notice for assistance. Thank you."
)

# ── Payment success ─────────────────────────────────────────────────────────

def payment_success(transaction_id: str, amount: Decimal) -> str:
    return (
        f"Your payment of ₹{amount:,.2f} has been processed successfully. "
        f"Your transaction ID is {transaction_id} — please keep this for your records. "
        "Thank you, and have a great day!"
    )

# ── Closing ─────────────────────────────────────────────────────────────────

CLOSING = (
    "Thank you for your time. Have a great day! Goodbye."
)

ABORTED = (
    "Understood. I've ended this session. "
    "If you need assistance in the future, please don't hesitate to call back. Goodbye."
)

# ── Fallback ────────────────────────────────────────────────────────────────

FALLBACK = "I didn't quite catch that. Could you please repeat?"

# Returned when an LLM / network call raises unexpectedly mid-turn. State is
# unchanged so the user can simply retry their last message — a transient
# upstream blip should not kill the agent loop or burn a retry counter.
TRANSIENT_ERROR = (
    "Sorry, I had a brief technical hiccup processing that. "
    "Could you please repeat your last message?"
)
