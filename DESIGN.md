# Cobrador — Design Document

## Executive Summary

Cobrador is a payment collection voice/text agent built for compliance-sensitive debt recovery. The core architecture uses a **deterministic finite state machine (FSM)** for all control flow, with the **LLM confined exclusively to structured field extraction** from natural language. This separation ensures predictable, auditable behavior while gracefully handling the messiness of real user input. Every FSM transition is guarded by an explicit allow-list; every outgoing message is templated and runs through a PII redaction layer; every API call is preceded by client-side validation.

---

## Architecture Overview

```xml
User Input
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│                      agent.py                           │
│              next(user_input: str) -> dict              │
│   - top-level exception boundary → TRANSIENT_ERROR      │
│   - PII redaction post-processor (allow_dob_readback)   │
└────────────────────────┬────────────────────────────────┘
                         │
          ┌──────────────▼──────────────┐
          │    core/state_machine.py    │
          │   ALLOWED_TRANSITIONS map   │
          │   InvalidTransitionError    │
          │   ConversationState + State │
          └──────────────┬──────────────┘
                         │
        ┌────────────────┼────────────────┐
        │                │                │
        ▼                ▼                ▼
┌──────────────┐  ┌─────────────┐  ┌──────────────┐
│ llm/         │  │ core/       │  │ tools/       │
│ extractors   │  │ verification│  │ payment_api  │
│ schemas      │  │ validators  │  │ (httpx +     │
│ prompts      │  │ normaliza-  │  │  tenacity)   │
│              │  │ tion        │  │              │
│ +            │  │ identity_   │  │              │
│ core/        │  │ regex (deterministic        │
│ identity_    │  │  pre-extractor for explicit │
│ regex.py     │  │  labeled patterns)          │
└──────────────┘  └─────────────┘  └──────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────┐
│                    output/                              │
│   responses.py (templated)  +  pii_filter.py (redact)  │
└─────────────────────────────────────────────────────────┘
```

**State flow**: `INIT → AWAITING_ACCOUNT_ID → LOOKING_UP_ACCOUNT → AWAITING_IDENTITY → VERIFYING → SHARE_BALANCE → AWAITING_AMOUNT → AWAITING_CARD → PROCESSING_PAYMENT → CONFIRM_AND_CLOSE | TERMINAL_*`

---

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Control flow | Deterministic FSM, not LLM-routed | LLM routing is unauditable in compliance flows; FSM transitions are guarded by `ALLOWED_TRANSITIONS` and any unlisted transition raises `InvalidTransitionError` so latent bugs surface in tests, not production |
| LLM scope | Per-state structured extraction only | Each prompt is single-purpose, few-shot, and pydantic-typed. Smaller prompts = higher accuracy + cheap unit-testability |
| Extraction backup | Deterministic regex pre-extractor | Reasoning models can silently drop fields in compound messages. `core/identity_regex.py` catches labeled patterns (name/DOB/Aadhaar/pincode) before the LLM runs; LLM fills gaps for unlabeled forms |
| Opportunistic capture | Each handler harvests volunteered info regardless of FSM position | Honors brief rule "do not re-ask for info already provided" without violating "do not skip steps" — fields are captured early, but FSM still walks every state in order |
| Name matching | Unicode-NFC exact match | Brief requires strict matching; case-insensitive would create partial-match fraud vectors |
| Verification retries | 3 attempts; **fields retained** across retries | A typo in one field (typically the name) is the most common failure mode for cooperative users. Wiping all fields forces re-confirmation of DOB the user already validated. The next-turn extractor overwrites whichever field they re-state; `verification_retries` counter still bounds brute-force attempts |
| Retry message | Suggests trying an alternate secondary factor | Can't reveal which field was wrong (privacy), but the message tells the user they can switch from DOB to Aadhaar/pincode if uncertain |
| Question handling | Asking a question during account-ID collection does **not** burn a retry | Distinguishes cooperative-but-confused users from junk input. Only an attempted-but-unparseable ID counts against the lookup-retry budget |
| Lookup transient error | Separate message from account-not-found | A 5xx after tenacity retried 3× is a technical issue, not a security signal. Different terminal message tells the caller to try again later vs. the enumeration-protected "unable to locate" |
| DOB confirm-back | "Is 14 May 1990 correct?" | Echoes **user-provided input** (to disambiguate DD/MM vs MM/DD), not account data. The account's stored DOB is never revealed in any agent message |
| PII filter exemption | `allow_dob_readback=True` only while `awaiting_dob_confirmation` | Otherwise the redaction layer would clobber the legitimate readback and the customer would see "[REDACTED]" with no way to confirm |
| Cardholder name | Collected from user | Auto-filling from account fails for legitimate third-party payers |
| Card data retention | Partial card retained across validation errors; cleared after API call | User may need 2–3 turns to provide all four fields. On each validation error, only the offending field is cleared, so the user re-enters just that one. `conv.clear_card()` runs immediately after `process_payment` returns |
| Response generation | Templated, never LLM-generated | Deterministic, testable, PII-safe by construction. Only the balance and transaction-id strings ever come from external data |
| Observability | Phoenix OTEL tracing via `phoenix.otel.register(auto_instrument=True)` | Every extractor call is traced (prompt, response, latency) so debugging eval failures takes seconds, not hours |

---

## Failure Handling Map

| Failure | Detection | User-visible response | FSM outcome |
|---------|-----------|----------------------|-------------|
| Account not found (404) | `lookup_account` 404 | Generic "couldn't locate" (enumeration-resistant) | Re-prompt, terminal after 3 |
| Lookup endpoint down | tenacity `ServerError` after 3 retries | "Temporary technical issue — try again later" | Terminal, distinct message |
| Verification mismatch | `verify_identity` strict compare | "Doesn't match — try alternate secondary factor" | Re-prompt, terminal after 3 |
| Ambiguous DOB format | Schema flag `dob_ambiguous=true` | "Please share DOB in a clear format" | Stays in AWAITING_IDENTITY |
| Invalid card (Luhn) | Client-side `luhn_check` before API | "Card number invalid — re-enter" | Stays in AWAITING_CARD; payment_retries +1 |
| Invalid CVV / expired card | Client-side validators before API | Field-specific message; offending field cleared | Stays in AWAITING_CARD; payment_retries +1 |
| Insufficient balance | Client-side `validate_amount` before API; also API 422 | "Amount exceeds balance" or terminal | Re-prompt amount or terminal |
| Payment API 5xx | tenacity retry → terminal after 3 | "Technical issue — call back" | Terminal payment failed |
| LLM/network blip mid-turn | Top-level `except Exception` in `next()` | "Brief hiccup — please repeat" | State unchanged; no retry burned |
| Empty / silent turn | Length check before LLM call | State-appropriate re-prompt | State unchanged; no LLM call, no retry burned |
| User says "cancel" | Schema `user_intent="wants_to_cancel"` in any extractor | Polite close | Terminal `USER_ABORTED` |
| Prompt injection attempt | Templated responses + LLM scope confined to extraction | Doesn't disclose stored data | Continues normally |

---

## Tradeoffs Accepted

| Tradeoff | Accepted Because |
|----------|-----------------|
| No persistence (fresh Agent per conversation) | Scope; Redis session resumption is a known follow-up |
| English-first | Brief examples are all English; the LLM tolerates light mixing but it's not guaranteed |
| Sync interface, no streaming | Simplifies state management; streaming can be added at the transport layer without changing FSM |
| Memory-only state, no Redis | Acceptable for single-process demo; not acceptable in production |
| GPT-5.4 for extraction | A fine-tuned small model would cut cost ~80%; deferred pending labeled dataset |
| Card details collected as plain text | Text/voice interface has no secure input channel; production would use tokenization (e.g. Stripe.js) so raw card data never reaches the agent |
| Card fields retained across turns inside `ConversationState.card` | Partial collection UX. Mitigations: card object dropped immediately after API call, never logged or serialized, offending field cleared on each validation failure, PII filter inspects every outgoing message |
| Verification retry retains all fields | Better UX for cooperative typo-recovery (real failure mode) at the cost of letting an attacker observe whether the *combination* failed rather than each field individually. The `verification_retries=3` cap still bounds brute force; the agent never says which field was wrong |
| No payment-API idempotency key | Sandbox-only constraint; in production an idempotency key from the upstream processor must be attached so a network retry doesn't double-charge. Currently we do **not** auto-retry post-submit network failures for this reason — the user sees a clear error and decides to retry manually |

---

## Assumptions

1. The external payment API is authoritative — Cobrador validates card length/checksum, CVV length, expiry, and amount before calling it, but does not independently validate BIN ranges.
2. "Full name" means the name string stored in the account record; the agent Unicode-NFC normalizes before comparison but does not tokenize or fuzzy-match.
3. A single conversation handles one payment transaction; multi-payment batching is out of scope.
4. The agent operates in English; minor Hindi mixing is tolerated by the LLM extractor but is not guaranteed.
5. Leap-year DOBs (e.g., 1988-02-29 / ACC1004) must be parsed correctly — covered by `python-dateutil` parsing, regex unit tests, and the `leap_year` persona.
6. Payment retries in the sandbox are safe to attempt on network errors. In production, payment retries require an idempotency key from the upstream processor; the agent therefore does **not** auto-retry the payment API after `process_payment` has been submitted — surfacing a clear error instead.
7. Echoing the user-provided DOB back for confirmation is **not** considered "exposing account data" per the brief, because the user just typed it. The account's stored DOB is never disclosed.

---

## What I'd Improve With More Time

1. **Compliance policy file** — FDCPA/RBI rules as declarative YAML consumed by a guardrails layer, so policy changes don't require code edits.
2. **Production telemetry** — Structured JSON logs + per-state latency dashboards + extraction-confidence tracking. Phoenix tracing covers dev/eval; production needs durable, queryable storage.
3. **Conversation resumption** — Redis-backed session store keyed on caller ID; same FSM state, durable across disconnects.
4. **Voice front-end** — The FSM core is transport-agnostic; plugging in Twilio + Deepgram reuses `agent.py` unchanged.
5. **Fine-tuned extraction model** — Replace GPT-5.4 in extractors with a small fine-tuned model; estimated ~80% cost reduction with equivalent accuracy on this narrow task.
6. **Idempotent payment retries** — Wire an idempotency key through `process_payment` so the agent can auto-retry post-submit network failures without risking duplicate charges.
7. **Persona-driven regression coverage** — Each new failure mode discovered in CLI smoke testing becomes a persona (e.g. `name_typo_recovery` was added after a CLI session surfaced field-retention regression). Goal: every shipped fix is pinned by a Tier 3 persona.
