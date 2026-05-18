# Cobrador — Design Document

## Architecture Overview

Cobrador is a payment-collection agent built on a **deterministic finite
state machine** that owns all flow control. The LLM is confined to
**structured field extraction** from messy natural language — it never
routes the conversation or generates user-facing copy. **90% of responses
are templated**, so PII safety is guaranteed by construction; only the
balance and transaction ID are interpolated from external data.

```
User input → Agent.next(str) → {"message": str}
                  │
                  ▼
        ┌──────────────────────┐
        │   FSM dispatcher     │  agent.py — entry point + lifecycle
        │   + exception        │  (snapshot-diff for no-progress detection,
        │     boundary         │   consecutive-transient-error guard)
        └──────────┬───────────┘
                   │
        ┌──────────▼───────────┐    ┌─────────────────────────┐
        │  Per-state handlers  │ ←→ │  core/state_machine.py  │
        │  (handlers.py mixin) │    │  ALLOWED_TRANSITIONS    │
        │                      │    │  InvalidTransitionError │
        └──────┬──┬────────┬───┘    └─────────────────────────┘
               │  │        │
       ┌───────┘  │        └─────────┐
       ▼          ▼                  ▼
  ┌─────────┐ ┌──────────┐  ┌────────────────┐
  │  llm/   │ │ tools/   │  │ output/        │
  │ extract │ │ payment_ │  │ responses (90% │
  │ schemas │ │ api      │  │ templated)     │
  │ prompts │ │ (httpx + │  │ pii_filter     │
  │         │ │ tenacity)│  │ (redaction)    │
  └─────────┘ └──────────┘  └────────────────┘
```

**State flow:** `INIT → AWAITING_ACCOUNT_ID → LOOKING_UP_ACCOUNT →
AWAITING_IDENTITY → VERIFYING → SHARE_BALANCE → AWAITING_AMOUNT →
AWAITING_CARD → PROCESSING_PAYMENT → CONFIRM_AND_CLOSE`, with six terminal
failure states (`ACCOUNT_NOT_FOUND`, `VERIFICATION_FAILED`,
`PAYMENT_FAILED`, `NO_PROGRESS`, `TRANSIENT_FAILURES`, `USER_ABORTED`).

---

## Key Decisions

| Decision | Choice | Why |
|---|---|---|
| Control flow | FSM with explicit allow-list, not LLM-routed | LLM routing is unauditable in compliance flows. Every transition checks `ALLOWED_TRANSITIONS`; any unlisted route raises `InvalidTransitionError` so latent bugs surface in tests, not production |
| LLM scope | Per-state structured extraction only (pydantic v2) | Smaller single-purpose prompts mean higher accuracy and trivial unit testability; templated responses can't leak PII by accident |
| Extraction backup | Deterministic regex pre-extractor | Reasoning models occasionally drop fields in compound first-turn messages. `core/identity_regex.py` catches labeled patterns; LLM fills gaps |
| Name matching | Unicode-NFC, **case-sensitive** | Brief explicitly forbids "case-insensitive workarounds for names". Messy lowercase input is normalized in the LLM extractor (rule 2 in the prompt), not at verification time |
| Verification retry message | Factor-agnostic ("re-check your name, or try Aadhaar/pincode") | Brief only protects DOB/Aadhaar/pincode, not name — but naming the failed field tells an attacker which secondary factor they got right, enabling elimination across 3 retries |
| DOB confirm-back | Echo **user-provided** date for parser disambiguation; PII filter exempts this one prompt via `allow_dob_readback=True` | Otherwise the customer sees `[REDACTED]` and can't confirm. The stored account DOB is never disclosed |
| Verification retries | 3 attempts; **fields retained** across retries | A typo in one field is the most common cooperative-user failure. Wiping all fields would force re-confirmation of DOB the user already validated; the counter still bounds brute force |
| Payment retry budgets | **Two independent counters** (3 each): `card_validation_retries` for user-fixable errors (Luhn / CVV / expiry / API 422), `payment_api_retries` for server-side 5xx | Brief asks us to "distinguish user-fixable errors from terminal failures" — sharing one counter conflates a typo with an outage |
| Idempotency | `Idempotency-Key` UUID header, regenerated per `_do_payment` entry, reused across tenacity retries | Tenacity may retry a successful-but-lost payment response; the key lets a real processor (Stripe/Razorpay) collapse retries into one logical charge. New card submission = new key (new intent) |
| Loop-termination bounds | `no_progress_turns` ≥ 5 → `TERMINAL_NO_PROGRESS`; `consecutive_transient_errors` ≥ 3 → `TERMINAL_TRANSIENT_FAILURES` | Cooperative users always advance one field per turn, so the first only fires on refusal / injection. The second closes the "LLM genuinely down" infinite-hiccup hole; resets on any successful turn |
| Observability | Phoenix OTEL traces + structured JSONL event log (`COBRADOR_EVENT_LOG=`) | Phoenix shows LLM spans; the JSONL carries application intent (FSM transitions, masked API payloads, field-by-field verification comparisons) for offline `jq` debugging |

---

## Tradeoffs Accepted

| Tradeoff | Why accepted |
|---|---|
| No conversation persistence (fresh `Agent()` per chat) | Out of scope; Redis-backed session resumption is the documented follow-up |
| English-first; light Hindi mixing tolerated but not guaranteed | Brief examples are all English; the prompt covers `naam` / `janam` keywords as a courtesy |
| GPT-5.4 in every extractor | A fine-tuned small model would cut cost ~80% with comparable accuracy on this narrow task; deferred pending a labeled dataset |
| Card data collected as plaintext in chat | Inherent to the text channel. Mitigations: card dropped from memory immediately after API call; logger masks card to last 4 and CVV to `***`; PII filter inspects every outgoing message |
| Verification retry retains all fields | Better UX for typo recovery vs giving an attacker the signal that the *combination* failed (rather than each field). The `verification_retries=3` cap still bounds brute force |
| No payment auto-retry on post-submit network failure | Without an upstream-provided idempotency token we can't safely re-submit; the user sees a clear error and can choose to retry. Our own `Idempotency-Key` covers tenacity-internal retries |

---

## What I Would Improve With More Time

1. **Conversation resumption** — Redis-backed session store keyed on caller ID, with cross-session rate-limiting so the per-`Agent()` retry budgets can't be defeated by spinning up fresh sessions.
2. **Compliance policy as declarative YAML** — FDCPA / RBI rules consumed by a guardrails layer; policy changes wouldn't require code edits.
3. **Voice front-end** — The FSM core is transport-agnostic; plugging in Twilio + Deepgram reuses `agent.py` unchanged.
4. **Fine-tuned extraction model** — Replace GPT-5.4 in extractors with a small fine-tuned model (~80% cost reduction at comparable accuracy on this narrow task).
5. **Production telemetry + cross-release baselines** — Per-state latency and extraction-confidence dashboards durably stored. Within-run statistical confidence on Tier 3 is already built (`--repeat N` reports mean ± stddev across runs); what's missing is *cross-release* persistence so a `mean_security` regression between v1.4 → v1.5 fails CI automatically.

---

### Assumptions & ambiguity resolutions

The brief invited us to document how we resolved ambiguity. Six calls:

1. **"Sensible retry limit"** → 3 attempts each for account-lookup,
   verification, card-validation, and payment-API. Industry-standard;
   bounded enough to stop brute force, generous enough for typos.
2. **"Strict matching, no fuzzy"** → Unicode-NFC + case-sensitive. Messy
   lowercase / all-caps is handled in the LLM extractor (Title-Case at
   extraction time), keeping the verification layer provably strict per
   the brief's literal text.
3. **"Reject incorrect attempts clearly" vs "Do not expose account data"** →
   Factor-agnostic retry message. Brief allows naming `name` (not on
   protected list), but doing so tells an attacker which secondary
   factor they got right. Stay factor-agnostic.
4. **Two-digit DOB years** → Always 19XX (a customer's birth year cannot
   be in the future). Card expiry years explicitly *not* subject to this
   rule (27 → 2027).
5. **Zero balance (ACC1003)** → Greet, verify, announce, auto-close.
   Don't skip verification just because nothing's owed — identity might
   still be sensitive.
6. **"Distinguish user-fixable from terminal failures"** → Two retry
   budgets: `card_validation_retries` for Luhn/CVV/expiry/422,
   `payment_api_retries` for 5xx. Each capped at 3; budgets don't bleed.

Other assumptions: the external API is authoritative (we validate
client-side first); "full name" is the stored string, NFC normalized,
never tokenized; one payment per conversation; echoing user-provided
DOB back for confirmation is not "exposing account data" — the user
just typed it.
