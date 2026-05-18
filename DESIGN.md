# Cobrador — Design Document

## Architecture Overview

Cobrador is a payment-collection agent. The core is a deterministic finite state
machine — the FSM owns all flow control. The LLM does exactly one thing: pull
structured fields out of messy natural language. It never routes the
conversation. It never writes what the caller hears. 90% of responses come from
templates, which means PII safety is structural — only balance and transaction
ID get interpolated from external data.

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

State flow: `INIT → AWAITING_ACCOUNT_ID → LOOKING_UP_ACCOUNT →
AWAITING_IDENTITY → VERIFYING → SHARE_BALANCE → AWAITING_AMOUNT →
AWAITING_CARD → PROCESSING_PAYMENT → CONFIRM_AND_CLOSE`, with six terminal
failure states (`ACCOUNT_NOT_FOUND`, `VERIFICATION_FAILED`,
`PAYMENT_FAILED`, `NO_PROGRESS`, `TRANSIENT_FAILURES`, `USER_ABORTED`).

---

## Key decisions

| Decision | Choice | Why |
|---|---|---|
| Control flow | FSM with explicit allow-list, not LLM-routed | LLM routing is unauditable in compliance flows. Every transition checks `ALLOWED_TRANSITIONS`; any unlisted route raises `InvalidTransitionError` so bugs surface in tests, not production. |
| LLM scope | Per-state structured extraction only (pydantic v2) | Smaller, single-purpose prompts are more accurate and trivially unit-testable. Templated responses can't leak PII by accident. |
| Extraction backup | Deterministic regex pre-extractor | Reasoning models sometimes drop fields in compound first-turn messages. `core/identity_regex.py` catches labeled patterns; LLM fills the gaps. |
| Name matching | Unicode-NFC, case-sensitive | The brief explicitly forbids "case-insensitive workarounds for names." Messy lowercase input gets normalized in the LLM extractor (rule 2 in the prompt), not at verification time. |
| Verification retry message | Factor-agnostic ("re-check your name, or try Aadhaar/pincode") | The brief protects DOB/Aadhaar/pincode but not name. Naming the failed field tells an attacker which secondary factor they got right, enabling elimination across 3 retries. |
| DOB confirm-back | Echo the user-provided date for parser disambiguation; PII filter exempts this one prompt via `allow_dob_readback=True` | Otherwise the customer sees `[REDACTED]` and can't confirm anything. The stored account DOB is never disclosed. |
| Verification retries | 3 attempts; fields retained across retries | A typo in one field is the most common cooperative-user failure. Wiping all fields forces re-confirmation of DOB the user already validated. The counter still bounds brute force. |
| Payment retry budgets | Two independent counters (3 each): `card_validation_retries` for user-fixable errors (Luhn/CVV/expiry/API 422), `payment_api_retries` for server-side 5xx | The brief asks us to "distinguish user-fixable errors from terminal failures." Sharing one counter conflates a typo with an outage. |
| Idempotency | `Idempotency-Key` UUID header, regenerated per `_do_payment` entry, reused across tenacity retries | Tenacity may retry a successful-but-lost payment response. The key lets a real processor (Stripe/Razorpay) collapse retries into one logical charge. New card submission = new key (new intent). |
| Loop-termination bounds | `no_progress_turns` ≥ 5 → `TERMINAL_NO_PROGRESS`; `consecutive_transient_errors` ≥ 3 → `TERMINAL_TRANSIENT_FAILURES` | Cooperative users always advance one field per turn, so the first only fires on refusal/injection. The second closes the "LLM genuinely down" infinite-hiccup hole; resets on any successful turn. |
| Observability | Phoenix OTEL traces + structured JSONL event log (`COBRADOR_EVENT_LOG=`) | Phoenix shows LLM spans; the JSONL carries application intent (FSM transitions, masked API payloads, field-by-field verification comparisons) for offline `jq` debugging. |

---

## Tradeoffs I accepted

| Tradeoff | Why |
|---|---|
| No conversation persistence (fresh `Agent()` per chat) | Out of scope. Redis-backed session resumption is the documented follow-up. |
| English-first; light Hindi mixing tolerated but not guaranteed | Brief examples are all English. The prompt covers `naam`/`janam` keywords as a courtesy. |
| GPT-5.4 default for extractors | Verified model-portable: `gpt-5.4-mini` runs at parity quality across the full pipeline (Tier 3: 4.77/5.0 task, 5.0/5.0 security, 100% completion, 0% PII leak; Tier 1.5: 31/31 messy cases). Three architectural choices make this work — regex pre-extractor catches labeled patterns before the LLM, pydantic structured outputs constrain output space, single-purpose prompts give narrow context. A fine-tuned small model would shave further; not needed for the brief's accuracy bar. |
| Card data collected as plaintext in chat | Inherent to the text channel. Mitigations: card dropped from memory immediately after API call; logger masks card to last 4 and CVV to `***`; PII filter inspects every outgoing message. |
| Verification retry retains all fields | Better UX for typo recovery vs. giving an attacker the signal that the combination failed rather than each field. The `verification_retries=3` cap still bounds brute force. |
| No payment auto-retry on post-submit network failure | Without an upstream-provided idempotency token we can't safely re-submit. The user sees a clear error and can choose to retry. Our own `Idempotency-Key` covers tenacity-internal retries. |

---

## What I'd improve with more time

1. **Conversation resumption** — Redis-backed session store keyed on caller ID, with cross-session rate-limiting so the per-`Agent()` retry budgets can't be defeated by spinning up fresh sessions.
2. **Compliance policy as declarative YAML** — FDCPA/RBI rules consumed by a guardrails layer. Policy changes without code edits.
3. **Voice front-end** — The FSM core is transport-agnostic. Plugging in Twilio + Deepgram reuses `agent.py` unchanged.
4. **Fine-tuned extraction model** — Replace GPT-5.4 with a small fine-tuned model for ~80% cost reduction at comparable accuracy.
5. **Production telemetry + cross-release baselines** — Per-state latency and extraction-confidence dashboards, durably stored. Within-run statistical confidence on Tier 3 is already built (`--repeat N` reports mean ± stddev across runs). What's missing is cross-release persistence so a `mean_security` regression between v1.4 → v1.5 fails CI automatically.

---

### Assumptions and ambiguity resolutions

The brief asked us to document where we made judgment calls. Six of them:

1. "Sensible retry limit" → 3 attempts each for account lookup, verification, card validation, and payment API. Standard industry practice — tight enough to stop brute force, loose enough for typos.
2. "Strict matching, no fuzzy" → Unicode-NFC + case-sensitive. Messy lowercase/all-caps is handled in the LLM extractor (Title-Case at extraction time). The verification layer stays provably strict per the brief's literal text.
3. "Reject incorrect attempts clearly" vs. "Do not expose account data" → Factor-agnostic retry message. The brief allows naming `name` (not on the protected list), but doing so tells an attacker which secondary factor they got right. Staying factor-agnostic is the safer call.
4. Two-digit DOB years → Always 19XX (a customer's birth year can't be in the future). Card expiry years are explicitly not subject to this rule (27 → 2027).
5. Zero balance (ACC1003) → Greet, verify, announce, auto-close. We don't skip verification just because nothing's owed — identity is still sensitive.
6. "Distinguish user-fixable from terminal failures" → Two retry budgets: `card_validation_retries` for Luhn/CVV/expiry/422, `payment_api_retries` for 5xx. Each capped at 3. Budgets don't bleed into each other.

Other assumptions: the external API is authoritative (we validate client-side first to avoid round-trips). "Full name" is the stored string, NFC normalized, never tokenized. One payment per conversation. Echoing user-provided DOB back for confirmation is not "exposing account data" — the user just typed it. The stored value is never disclosed.
