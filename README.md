# Cobrador — Payment Collection AI Agent

> *Cobrador* is Spanish/Portuguese for "collector." Cobrador is a deterministic FSM-driven conversational agent that verifies customer identity and processes loan repayments over a text (or voice-ready) channel.

---

## Quick Start

**Requirements**: Python 3.12+, [`uv`](https://docs.astral.sh/uv/)

```bash
# Clone and install
git clone https://github.com/ree2raz/cobrador-payment-collection-ai-agent
cd cobrador-payment-collection-ai-agent
uv sync

# Set your OpenAI API key
export OPENAI_API_KEY=sk-...

# Run interactive REPL
uv run python cli.py
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
│   ├── personas.py        # 10 simulation personas
│   ├── simulator.py       # LLM-driven user simulator
│   ├── judge.py           # LLM-as-judge scoring
│   ├── messy_cases.py     # 21 production-style messy extraction test cases
│   └── run_eval.py        # Three-tier eval runner CLI
└── tests/                 # 104 tests (Tier 1 + Tier 2, 100% passing)
    └── test_extraction_messy.py  # Tier 1.5: live LLM messy extraction tests
```

---

## Running Evaluations

```bash
# Tier 1 + 2: unit and scripted scenario tests (no API key needed)
uv run pytest tests/ -v

# Tier 3: persona simulation with LLM-as-judge
uv run python -m eval.run_eval --tier 3

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
| Validator correctness (Luhn, date, CVV, amount) | 24 | ✅ 100% |
| Verification truth table (16-row name × DOB × Aadhaar × pincode) | 16 | ✅ 100% |
| State machine transition allow-list | 8 | ✅ 100% |
| PII filter — no leakage under any input | 12 | ✅ 100% |
| Scripted multi-turn scenarios (all 4 accounts + all failure paths) | 44 | ✅ 100% |
| **Total** | **104** | **✅ 104/104** |

### Tier 1.5 — Messy Extraction Accuracy

21 production-style inputs covering verbal numbers, Hinglish, self-correction, honorifics,
ambiguous dates, full 12-digit Aadhaar, leap-year DOB.

| Extractor | Cases | Result |
|-----------|-------|--------|
| account_id (lowercase, hyphenated, hesitant, Hinglish) | 4/4 | ✅ 100% |
| name (filler words, Hinglish, self-correction, honorific stripped) | 4/4 | ✅ 100% |
| dob (verbal, Hinglish, DD-MM-YYYY, ambiguous flagged, leap year) | 5/5 | ✅ 100% |
| aadhaar (full 12-digit → last 4, verbal digits) | 2/2 | ✅ 100% |
| amount (words, ₹ symbol, "pay it all") | 3/3 | ✅ 100% |
| card (spaced number, verbal CVV, verbal expiry) | 3/3 | ✅ 100% |
| **Total** | **21/21** | **✅ 100%** |

### Tier 3 — Persona Simulation (10 personas, LLM-as-judge)

| Metric | Score | Notes |
|--------|-------|-------|
| Task completion | **4.2 / 5.0** | 7/10 perfect; 2 adversarial personas correctly fail |
| Security (PII protection) | **5.0 / 5.0** | 0 stored account secrets leaked across all turns |
| Politeness | **5.0 / 5.0** | — |
| Clarity | **4.5 / 5.0** | — |
| PII leak rate | **0%** | Verified across 47 turns |
| Completion rate | **100%** | All 10 conversations reached a terminal state |
| Mean turns to completion | **4.7** | — |
| Adversarial imposter rejected | **100%** | Reaches `TERMINAL_VERIFICATION_FAILED` |
| Prompt injection blocked | **100%** | No stored account data disclosed |

> The 4.2 mean task completion is pulled down by the `prompt_injector` (task=1, correct —
> the agent should not help an injector) and `confused` persona (task=2, the simulator
> declined to share card details in a text chat, an inherent limitation of text-based
> card collection documented in DESIGN.md).

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
| `python-dateutil>=2.9.0` | Robust date parsing (handles leap years) |
| `structlog>=24.1.0` | Structured logging |

Dev: `pytest`, `pytest-asyncio`, `pytest-cov`, `respx`, `arize-phoenix`, `arize-phoenix-otel`, `openinference-instrumentation-openai`
