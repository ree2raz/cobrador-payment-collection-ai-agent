# Cobrador — Design Document

## Executive Summary

Cobrador is a payment collection voice/text agent built for compliance-sensitive debt recovery. The core architecture uses a **deterministic finite state machine (FSM)** for all control flow, with a **LLM confined exclusively to structured field extraction** from natural language. This separation ensures predictable, auditable behavior while handling the messiness of real user input.

---

## Architecture Overview

```xml
User Input
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│                      agent.py                           │
│              next(user_input: str) -> dict              │
└────────────────────────┬────────────────────────────────┘
                         │
          ┌──────────────▼──────────────┐
          │    core/state_machine.py    │
          │  FSM owns ALL transitions   │
          │  ConversationState + State  │
          └──────────────┬──────────────┘
                         │
        ┌────────────────┼────────────────┐
        │                │                │
        ▼                ▼                ▼
┌──────────────┐  ┌─────────────┐  ┌──────────────┐
│ llm/         │  │ core/       │  │ tools/       │
│ extractors   │  │ verification│  │ payment_api  │
│ (per-state   │  │ validators  │  │ (httpx +     │
│ GPT-4o       │  │ normaliza-  │  │  tenacity)   │
│ structured   │  │ tion)       │  └──────────────┘
│ outputs)     │  └─────────────┘
└──────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────┐
│                    output/                              │
│   responses.py (templated)  +  pii_filter.py (redact)  │
└─────────────────────────────────────────────────────────┘
```

**State flow**: INIT → AWAITING_ACCOUNT_ID → LOOKING_UP_ACCOUNT → AWAITING_IDENTITY → VERIFYING → SHARE_BALANCE → AWAITING_AMOUNT → AWAITING_CARD → PROCESSING_PAYMENT → CONFIRM_AND_CLOSE | TERMINAL_*

---

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Control flow | FSM, not LLM | Non-deterministic LLM routing is unacceptable in compliance-sensitive payment flows; FSM is auditable and testable |
| LLM scope | Extraction only | Smaller, focused per-state prompts improve accuracy and make unit testing straightforward |
| Name matching | Unicode-NFC exact match | Spec requirement; case-insensitive matching would allow partial-match fraud vectors |
| Verification retries | 3 combined attempts | Balances usability against harassment avoidance, privacy protection, and fraud prevention |
| Cardholder name | Collected from user | Auto-filling from account name fails for legitimate third-party payers |
| DOB confirm-back | "Is 14 May 1990 correct?" | Prevents silent misparse of ambiguous formats (DD/MM vs MM/DD) |
| Lookup failure message | Generic ("unable to locate") | Prevents account enumeration by external actors |
| Response generation | Templated (not LLM) | Deterministic, testable, and PII-safe by construction |
| PII defense | Three-layer | Extraction prompt rules + state never stores full Aadhaar + output filter redaction |

---

## Tradeoffs Accepted

| Tradeoff | Accepted Because |
|----------|-----------------|
| No persistence (fresh Agent per conversation) | Scope; Redis session resumption is a known follow-up |
| English only | Persona tests surfaced Hinglish; multi-language extraction is a next priority |
| Sync interface, no streaming | Simplifies state management; streaming can be added at the transport layer without changing FSM |
| Memory-only state, no Redis | Acceptable for single-process demo; not acceptable in production |
| GPT-4o for extraction | Fine-tuned small model would cut cost ~80%; deferred pending labeled dataset |

---

## Assumptions

1. The external payment API is authoritative — Cobrador does not independently validate card BINs beyond Luhn.
2. "Full name" means the name string stored in the account record; the agent performs Unicode-NFC normalization before comparison but does not tokenize or fuzzy-match.
3. A single conversation handles one payment transaction; multi-payment batching is out of scope.
4. The agent operates in English; Hinglish or regional-language input will be partially handled by the LLM extractor but is not guaranteed.
5. Leap-year DOBs (e.g., 1988-02-29) are valid and must be parsed correctly — covered by `python-dateutil`.

---

## What I'd Improve With More Time

1. **Compliance policy file** — FDCPA/RBI rules as declarative YAML consumed by a guardrails layer, so policy changes don't require code edits.
2. **Telemetry & observability** — Structured logs (already using `structlog`) piped to a PIE-style analytics dashboard; per-state latency and extraction confidence tracking.
3. **Multi-language extraction** — Hinglish and regional-language support in extraction prompts; detected in persona simulation tests.
4. **Conversation resumption** — Redis-backed session store keyed on caller ID; same FSM state, durable across disconnects.
5. **Voice front-end** — The FSM core is transport-agnostic; plugging in a Twilio/Deepgram front-end reuses agent.py unchanged (same pattern as the dental-desk-voice-agent reference).
6. **Fine-tuned extraction model** — Replace GPT-4o in extractors with a small fine-tuned model; estimated ~80% cost reduction with equivalent accuracy on this narrow task.
7. **Adversarial robustness suite** — Extend Tier 3 eval with systematic prompt injection and enumeration attack scenarios beyond the current `prompt_injector` and `adversarial_imposter` personas.
