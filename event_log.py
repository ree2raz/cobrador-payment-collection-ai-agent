"""
Structured JSONL event logging for offline debugging.

Phoenix shows LLM spans but not our application-level intent: which FSM
transition fired, what payload went to the payment API, what the verifier
compared. This module fills that gap by appending one JSON record per
event to a file you can grep / jq.

Enable by setting COBRADOR_EVENT_LOG=<path>:

    COBRADOR_EVENT_LOG=./logs/events.jsonl uv run python cli.py

Every record carries `ts`, `conv` (12-char conversation ID), and `event`
type, plus event-specific fields. PII handling:
- Card numbers logged as last-4 only
- CVV always masked
- DOB / Aadhaar / pincode are logged in full because the whole point of
  this file is debugging verification failures. Treat the log file as
  sensitive and gitignore it.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()


# Event type names — import these constants instead of using string literals,
# so a typo surfaces as NameError instead of silently dropping the log line.
EVENT_CONVERSATION_START = "conversation_start"
EVENT_TURN_START = "turn_start"
EVENT_TURN_END = "turn_end"
EVENT_TURN_ERROR = "turn_error"
EVENT_STATE_TRANSITION = "state_transition"
EVENT_LLM_EXTRACT = "llm_extract"
EVENT_API_REQUEST = "api_request"
EVENT_API_RESPONSE = "api_response"
EVENT_VERIFICATION = "verification"


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, date):
        return obj.isoformat()
    return repr(obj)


class EventLog:
    def __init__(self) -> None:
        self._path: Path | None = None
        self._enabled = False
        self._conversation_id: str = self._new_id()
        self._configure_from_env()

    def _configure_from_env(self) -> None:
        raw = os.environ.get("COBRADOR_EVENT_LOG")
        if not raw:
            return
        self._path = Path(raw).expanduser()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._enabled = True

    @staticmethod
    def _new_id() -> str:
        return uuid.uuid4().hex[:12]

    def new_conversation(self) -> str:
        """Reset the conversation ID. Called by Agent.__init__."""
        self._conversation_id = self._new_id()
        return self._conversation_id

    @property
    def conversation_id(self) -> str:
        return self._conversation_id

    @property
    def enabled(self) -> bool:
        return self._enabled

    def emit(self, event: str, **fields: Any) -> None:
        if not self._enabled or self._path is None:
            return
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "conv": self._conversation_id,
            "event": event,
            **fields,
        }
        line = json.dumps(record, default=_json_default, ensure_ascii=False)
        with _LOCK:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")


event_log = EventLog()


def mask_card_number(cn: str | None) -> str:
    if not cn:
        return ""
    return f"****{cn[-4:]}" if len(cn) >= 4 else "****"


# Match any 12+ consecutive digits (with optional internal spaces / hyphens)
# — these are almost certainly card numbers. Replace with masked last-4.
import re as _re

_CARD_LIKE_RE = _re.compile(r"(?:\d[\s-]*){12,}\d?")


def mask_card_substrings(text: str) -> str:
    """Find card-like digit sequences anywhere in a string and mask all but
    the trailing 4 digits. Used to scrub raw user_input before it lands in
    the JSONL log. The brief explicitly forbids logging raw card data."""
    if not text:
        return text

    def _sub(match: _re.Match) -> str:
        digits = _re.sub(r"\D", "", match.group(0))
        if len(digits) < 12:
            return match.group(0)
        return f"****{digits[-4:]}"

    return _CARD_LIKE_RE.sub(_sub, text)


def mask_cvv_substrings(text: str) -> str:
    """Best-effort scrub of CVV mentions. Catches 'CVV is 123', 'cvv: 1234',
    and verbal-CVV phrases like 'CVV one two three'."""
    if not text:
        return text
    # Numeric CVV after the word 'cvv'
    text = _re.sub(r"(?i)\bcvv\b[^\d]{0,8}\d{3,4}", "CVV ***", text)
    # Verbal CVV — three or four single digit words after 'cvv'
    digit_word = r"(?:zero|one|two|three|four|five|six|seven|eight|nine)"
    text = _re.sub(
        rf"(?i)\bcvv\b[\s:is]*(?:{digit_word}\s+){{2,3}}{digit_word}",
        "CVV ***",
        text,
    )
    return text
