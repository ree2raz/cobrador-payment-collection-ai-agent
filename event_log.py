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
