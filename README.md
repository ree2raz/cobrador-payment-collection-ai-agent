# Cobrador — Payment Collection AI Agent

> *Cobrador* is Spanish/Portuguese for "collector." Cobrador is a deterministic FSM-driven conversational agent that verifies customer identity and processes loan repayments over a text (or voice-ready) channel.

---

## Quick Start

**Requirements**: Python 3.12+, [`uv`](https://docs.astral.sh/uv/)

```bash
# Clone and install
git clone <repo>
cd prodigal-agent-engineer--payment-voice-agent
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
│   ├── client.py          # OpenAI client wrapper
│   ├── schemas.py         # Pydantic v2 structured output models
│   ├── extractors.py      # Per-state extractor functions
│   └── prompts.py         # Extraction prompt templates (few-shot)
├── tools/
│   └── payment_api.py     # httpx + tenacity retry for lookup & payment
├── output/
│   ├── responses.py       # Templated user-facing messages
│   └── pii_filter.py      # Final output PII redaction layer
├── eval/
│   ├── personas.py        # 10 simulation personas
│   ├── simulator.py       # LLM-driven user simulator
│   ├── judge.py           # LLM-as-judge scoring
│   └── run_eval.py        # Three-tier eval runner CLI
└── tests/                 # 104 tests (100% passing)
```

---

## Running Evaluations

```bash
# Tier 1 + Tier 2: unit and scripted scenario tests
uv run pytest tests/ -v

# With coverage report
uv run pytest tests/ --cov=. --cov-report=term-missing

# Tier 3: persona simulation (requires OPENAI_API_KEY)
uv run python eval/run_eval.py --tier 3

# Full eval pipeline
uv run python eval/run_eval.py --tier all
```

---

## Eval Metrics

| Tier | Scope | Metric | Target |
|------|-------|--------|--------|
| 1 | Unit | Validator correctness | 100% |
| 1 | Unit | Verification truth table (16+ cases) | 100% |
| 1 | Unit | PII filter — no leakage | 100% |
| 1 | Unit | State machine transitions | 100% |
| 2 | Scripted scenarios | All multi-turn flows pass | 104/104 |
| 2 | Scripted scenarios | LLM mock extraction accuracy | 100% |
| 3 | Persona simulation | Task completion (cooperative) | ≥95% |
| 3 | Persona simulation | Verification refusal (adversarial_imposter) | 100% |
| 3 | Persona simulation | Prompt injection blocked | 100% |
| 3 | Persona simulation | LLM-as-judge score (avg across personas) | ≥4.0/5.0 |

---

## Test Accounts

| Account ID | Name | DOB | Aadhaar Last 4 | Pincode | Balance |
|------------|------|-----|----------------|---------|---------|
| ACC1001 | Nithin Jain | 1990-05-14 | 4321 | 400001 | ₹1250.75 |
| ACC1002 | Rajarajeswari Balasubramaniam | 1985-11-23 | 9876 | 400002 | ₹540.00 |
| ACC1003 | Priya Agarwal | 1992-08-10 | 2468 | 400003 | ₹0.00 |
| ACC1004 | Rahul Mehta | 1988-02-29 | 1357 | 400004 | ₹3200.50 |

---

## Sample Conversations

### 1. Happy Path

```
Agent : Hello! I'm calling from Prodigal Finance regarding an outstanding loan account.
        Could you please share your account ID?

User  : ACC1001

Agent : Thank you. To verify your identity, could I please have your full name as
        registered on the account?

User  : Nithin Jain

Agent : Thank you, Nithin. Could you also confirm your date of birth?

User  : 14th May 1990

Agent : Is 14th May 1990 correct?

User  : Yes

Agent : Identity verified. Your current outstanding balance is ₹1,250.75.
        How much would you like to pay today?

User  : I'll pay the full amount.

Agent : Got it — ₹1,250.75. Please share your card number.

User  : 4111111111111111

Agent : And the expiry date?

User  : 12/26

Agent : And the CVV?

User  : 123

Agent : Lastly, the name on the card?

User  : Nithin Jain

Agent : Processing your payment of ₹1,250.75...
        Payment successful! Transaction ID: TXN-XXXXXX.
        Thank you, Nithin. Have a great day!
```

---

### 2. Verification Failure (3 retries exhausted)

```
Agent : Could you please share your account ID?

User  : ACC1002

Agent : Thank you. Could I please have your full name as registered on the account?

User  : Raja Bala

Agent : I'm sorry, I wasn't able to verify that. Could you please try again with your
        full name and date of birth or Aadhaar last 4 or pincode?

User  : Rajarajeswari, DOB 23 Nov 1985

Agent : I'm sorry, I wasn't able to verify that. One more attempt — please share your
        full name and one of: date of birth, Aadhaar last 4 digits, or pincode.

User  : Rajarajeswari Balasubramaniam, pin 400003

Agent : I'm sorry, we were unable to verify your identity after multiple attempts.
        Please contact our support team for assistance. Goodbye.
```

*Note: Failure on attempt 3 uses generic message regardless of reason (fraud prevention).*

---

### 3. Payment Failure (card declined)

```
Agent : Your outstanding balance is ₹540.00. How much would you like to pay today?

User  : 540

Agent : Got it — ₹540.00. Please share your card number.

User  : 4000000000000002

Agent : And the expiry date?

User  : 09/25

Agent : And the CVV?

User  : 456

Agent : And the name on the card?

User  : Rajarajeswari Balasubramaniam

Agent : I'm sorry, your payment of ₹540.00 could not be processed. Please check your
        card details or try a different card. Would you like to try again?

User  : No, that's fine.

Agent : Understood. Please contact us when you're ready to complete the payment.
        Goodbye!
```

---

### 4. Zero Balance Edge Case

```
Agent : Could you please share your account ID?

User  : ACC1003

Agent : Thank you. Could I please have your full name?

User  : Priya Agarwal

Agent : Could you confirm your date of birth or Aadhaar last 4 or pincode?

User  : pincode 400003

Agent : Identity verified. Your current outstanding balance is ₹0.00.
        There is no outstanding amount on this account. Have a great day!
```

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
| `openai>=1.30.0` | GPT-4o structured extraction via `responses.parse` |
| `httpx>=0.27.0` | Async-capable HTTP client for payment API |
| `pydantic>=2.7.0` | Structured output models (v2) |
| `tenacity>=8.3.0` | Retry logic for API calls |
| `python-dateutil>=2.9.0` | Robust date parsing |
| `structlog>=24.1.0` | Structured logging |
