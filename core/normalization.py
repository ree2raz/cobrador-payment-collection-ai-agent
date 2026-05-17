import unicodedata
import re


def normalize_name(name: str) -> str:
    """Unicode-NFC normalize + collapse whitespace. No case change."""
    normalized = unicodedata.normalize("NFC", name)
    return re.sub(r"\s+", " ", normalized).strip()


def normalize_account_id(raw: str) -> str | None:
    """Strip whitespace/hyphens, uppercase. Returns None if not ACC format."""
    cleaned = raw.upper().replace(" ", "").replace("-", "")
    if not re.match(r"^ACC\d+$", cleaned):
        return None
    return cleaned


def normalize_pincode(raw: str) -> str:
    """Remove spaces from pincode (handles '4 0 0 0 0 1' → '400001')."""
    return raw.replace(" ", "").strip()


def normalize_card_number(raw: str) -> str:
    """Strip all non-digit characters."""
    return re.sub(r"\D", "", raw)


def normalize_cvv(raw: str) -> str:
    return re.sub(r"\D", "", raw)
