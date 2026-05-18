# Cobrador — Payment Collection AI Agent

> *Cobrador* is Spanish/Portuguese for "collector." Cobrador is a deterministic FSM-driven conversational agent that verifies customer identity and processes loan repayments over a text (or voice-ready) channel.

---

## For the Prodigal Evaluator — Quick Plug-in

Three steps to wire Cobrador into your LLM-based evaluator. Total setup: under a minute.

```bash
# 1. Install (Python 3.12+ and uv required)
git clone https://github.com/ree2raz/cobrador-payment-collection-ai-agent
cd cobrador-payment-collection-ai-agent
uv sync

# 2. Set the OpenAI key (used only by the agent's extractors)
export OPENAI_API_KEY=sk-...

# 3. Sanity check — runs all 183 deterministic tests in ~1s, no API calls
uv run pytest -m "not integration"
```

**Calling the agent — matches the brief's interface exactly:**

```python
from agent import Agent

agent = Agent()                          # No args required
response = agent.next("Hi")              # → {"message": "Hello! …"}
response = agent.next("My account is ACC1001")
response = agent.next("Nithin Jain")
# ... loop until response indicates terminal state
```

**Contract guarantees your evaluator can rely on:**

- `Agent()` takes no arguments; one instance = one conversation. Spin up a fresh `Agent()` for each persona.
- `agent.next(str) -> {"message": str}` — exactly one turn per call, state persists internally, no manual resets needed between turns.
- `agent._conv.state` exposes a `State` enum (from `core.state_machine`) for terminal-state detection. The `TERMINAL_STATES` set in the same module is the canonical "conversation is over" check.
- Deterministic: FSM transitions are deterministic by construction; LLM extractors run at `temperature=0.0` with pydantic-validated structured outputs.
- No external setup between turns. No file writes, no network state. Phoenix tracing and the JSONL event log are opt-in via env vars and don't affect agent behavior.

**Test accounts** (lookup-account API responds with these in the sandbox):

| Account ID | Name | DOB | Aadhaar Last 4 | Pincode | Balance |
|---|---|---|---|---|---|
| ACC1001 | Nithin Jain | 1990-05-14 | 4321 | 400001 | ₹1,250.75 |
| ACC1002 | Rajarajeswari Balasubramaniam | 1985-11-23 | 9876 | 400002 | ₹540.00 |
| ACC1003 | Priya Agarwal (zero balance) | 1992-08-10 | 2468 | 400003 | ₹0.00 |
| ACC1004 | Rahul Mehta (leap-year DOB) | 1988-02-29 | 1357 | 400004 | ₹3,200.50 |

**If you'd like to run our own eval framework alongside yours:**

```bash
uv run python -m eval.run_eval --tier 3                # one run, 13 personas
uv run python -m eval.run_eval --tier 3 --repeat 5     # N=5, mean ± stddev
uv run python -m eval.run_eval --tier all --messy      # full pipeline
```

See [EVALUATION.md](./EVALUATION.md) for the full eval design and observations on where the agent struggles. See [DESIGN.md](./DESIGN.md) for architecture + key decisions.

---

## Quick Start (interactive use)

```bash
# After the install steps above:
uv run python cli.py    # Interactive REPL
```

---

## Project Structure

```
cobrador/
├── agent.py               # Agent class — next(user_input) -> dict
├── cli.py                 # Interactive REPL
├── core/
│   ├── state_machine.py   # State enum, ConversationState, transitions
│   ├── verification.py    # Name + secondary factor verification
│   ├── validators.py      # Luhn, date, amount, CVV validators
│   └── normalization.py   # Unicode-NFC, whitespace collapse
├── llm/
│   ├── client.py          # OpenAI client wrapper (gpt-5.4 / gpt-5.4-mini)
│   ├── schemas.py         # Pydantic v2 structured output models
│   ├── extractors.py      # Per-state extractor functions
│   └── prompts.py         # Extraction prompt templates (few-shot)
├── tools/
│   └── payment_api.py     # httpx + tenacity retry for lookup & payment
├── output/
│   ├── responses.py       # Templated user-facing messages (90% of output)
│   └── pii_filter.py      # Final output PII redaction layer
├── eval/
│   ├── personas.py        # 13 simulation personas
│   ├── simulator.py       # LLM-driven user simulator
│   ├── judge.py           # LLM-as-judge scoring
│   ├── messy_cases.py     # 21 production-style messy extraction test cases
│   └── run_eval.py        # Three-tier eval runner CLI
└── tests/                 # 183 deterministic tests passing + 23 live LLM tests skipped offline
    └── test_extraction_messy.py  # Tier 1.5: live LLM messy extraction tests
```

---

## Evaluation

See [EVALUATION.md](./EVALUATION.md) for the full evaluation approach:
test-case design, correctness definitions per step, automated eval
script, and honest observations on where the agent struggles.

## Running Evaluations

```bash
# Tier 1 + 2: unit and scripted scenario tests (no API key needed)
uv run pytest tests/ -v

# Tier 3: persona simulation with LLM-as-judge
uv run python -m eval.run_eval --tier 3

# Tier 3 with statistical rigor — run N times, report mean ± stddev
# (LLM-judge variance is real; N=3-5 gives reliable confidence for claims)
uv run python -m eval.run_eval --tier 3 --repeat 5

# Tier 3 — run specific personas only
uv run python -m eval.run_eval --tier 3 --personas cooperative rambling adversarial_imposter

# Messy extraction accuracy (21 production-style inputs)
uv run python -m eval.run_eval --messy

# Full pipeline
uv run python -m eval.run_eval --tier all --messy

# With Phoenix observability
PHOENIX=1 uv run python -m eval.run_eval --tier 3
```

---

## Eval Results

### Tier 1 + 2 — Unit & Scripted Tests

| Suite | Tests | Result |
|-------|-------|--------|
| Normalization helpers | 14 | ✅ 100% |
| Validator correctness (Luhn, date, CVV, amount) | 31 | ✅ 100% |
| Verification truth table + account-specific cases | 23 | ✅ 100% |
| State machine transition allow-list | 13 | ✅ 100% |
| PII filter — DOB/Aadhaar/pincode variants | 17 | ✅ 100% |
| API payload/retry/idempotency-key behavior | 4 | ✅ 100% |
| Identity-regex deterministic pre-extractor | 21 | ✅ 100% |
| Event-log masking + event-constant uniqueness | 22 | ✅ 100% |
| Scripted multi-turn scenarios (all 4 accounts + failure paths + no-progress + retry-budget splits + transient-error termination) | 34 | ✅ 100% |
| **Total** | **183** | **✅ 183/183** |

### Tier 1.5 — Messy Extraction Accuracy

23 production-style inputs covering brief's exact phrasings (verbal CVV digits,
spaced pincode, nickname-vs-full-name, leap-year DOB, ambiguous dates, hesitant
account-ID phrasing).

| Extractor | Cases | Result |
|-----------|-------|--------|
| account_id (lowercase, hyphenated, hesitant) | 4/4 | ✅ 100% |
| name (filler words, self-correction, honorific, nickname-vs-full) | 4/4 | ✅ 100% |
| dob (verbal, DD-MM-YYYY, ambiguous flagged, leap year) | 5/5 | ✅ 100% |
| aadhaar (full 12-digit → last 4, labeled last-4) | 2/2 | ✅ 100% |
| amount (words, ₹ symbol, "pay it all") | 3/3 | ✅ 100% |
| card (spaced number, verbal CVV, verbal expiry, compound) | 5/5 | ✅ 100% |
| **Total** | **23/23** | **✅ 100%** |

### Tier 3 — Persona Simulation (13 personas, LLM-as-judge)

Personas: `cooperative`, `rambling`, `terse`, `confused`, `adversarial_imposter`,
`prompt_injector`, `zero_balance` (ACC1003), `invalid_card`, `leap_year` (ACC1004),
`out_of_order`, `turn1_volunteer`, `name_typo_recovery`,
`api_failure_during_payment` (fault-injects payment-API 5xx).

Latest run across all 13 personas (`tier3_20260518_050925.json`):

| Metric | Score | Notes |
|--------|-------|-------|
| Task completion | **4.67 / 5.0** | Judge scores against `expected_outcome` — adversarial verification_failure = 5, prompt_injector pii_not_leaked = 5 |
| Security (PII protection) | **5.0 / 5.0** | 0 stored account secrets leaked across all turns |
| Politeness | **5.0 / 5.0** | — |
| Clarity | **4.83 / 5.0** | — |
| PII leak rate | **0%** | Verified across all turns; DOB confirm-back correctly exempted |
| Completion rate | **100%** | All 12 conversations reached a terminal state — refusal loops now bounded by `TERMINAL_NO_PROGRESS` |
| Mean turns to completion | **4.92** | Down from 8.0 once no-progress termination shipped |
| Adversarial imposter rejected | **100%** | Reaches `TERMINAL_VERIFICATION_FAILED` in 5 turns |
| Prompt injection blocked | **100%** | No stored account data disclosed; closes via no-progress on persistent refusal |

---

## Test Accounts

| Account ID | Name | DOB | Aadhaar Last 4 | Pincode | Balance |
|------------|------|-----|----------------|---------|---------|
| ACC1001 | Nithin Jain | 1990-05-14 | 4321 | 400001 | ₹1,250.75 |
| ACC1002 | Rajarajeswari Balasubramaniam | 1985-11-23 | 9876 | 400002 | ₹540.00 |
| ACC1003 | Priya Agarwal | 1992-08-10 | 2468 | 400003 | ₹0.00 |
| ACC1004 | Rahul Mehta | 1988-02-29 | 1357 | 400004 | ₹3,200.50 |

---

## Sample Conversations

See [CONVERSATIONS.md](./CONVERSATIONS.md) for 9 annotated conversations:

1. Cooperative user — DOB verification, partial payment (ACC1001)
2. Rambling user — Aadhaar verification, full balance (ACC1002)
3. Out-of-order — name + DOB volunteered together (ACC1001)
4. Verification failure — wrong details, 3 retries exhausted
5. Verification failure — adversarial imposter
6. Payment failure — invalid card, retry succeeds
7. Payment failure — expired card, re-entered
8. Edge case — leap year DOB (ACC1004, 1988-02-29)
9. Edge case — prompt injection attempt (agent holds its ground)

---

## Observability

Traces every LLM call via [Arize Phoenix](https://arize.com/docs/phoenix/).

**Persistent setup** (recommended — traces survive process exit):
```bash
# Terminal 1 — keep running
uv run python -m phoenix.server.main serve

# Terminal 2 — run eval or CLI
PHOENIX=1 uv run python -m eval.run_eval --tier 3
```

**One-shot** (in-process server, traces lost on exit):
```bash
PHOENIX=1 uv run python cli.py
```

Browse traces at **http://localhost:6006**.

### Structured event log (JSONL)

Phoenix shows LLM spans but not application intent. For offline debugging
(verification mismatches, FSM transitions, payment-API payloads), set
`COBRADOR_EVENT_LOG` to a path and one JSON record per event will be
appended:

```bash
COBRADOR_EVENT_LOG=./logs/events.jsonl uv run python cli.py

# Then inspect with jq:
jq 'select(.event == "verification")' logs/events.jsonl
jq 'select(.event == "state_transition") | "\(.from_state) -> \(.to_state)"' logs/events.jsonl
```

Event types: `conversation_start`, `turn_start`, `turn_end`, `turn_error`,
`state_transition`, `llm_extract`, `api_request`, `api_response`,
`verification`. Card numbers logged last-4 only; CVV always masked. The
log file contains DOB / Aadhaar / pincode in clear — gitignored by default.

---

## API Endpoints

Base URL: `https://se-payment-verification-api.service.external.usea2.aws.prodigaltech.com/`

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/lookup-account` | Fetch account details by account ID |
| POST | `/api/process-payment` | Submit card payment for an account |

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `openai>=1.30.0` | GPT-5.4 structured extraction via `responses.parse` |
| `httpx>=0.27.0` | HTTP client for payment API |
| `pydantic>=2.7.0` | Structured output models (v2) |
| `tenacity>=8.3.0` | Retry with exponential backoff on API calls |
Dev: `pytest`, `pytest-cov`, `arize-phoenix`, `arize-phoenix-otel`, `openinference-instrumentation-openai`
