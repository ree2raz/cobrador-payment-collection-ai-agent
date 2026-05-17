"""
Extraction prompt templates — one per state that needs LLM parsing.
Response generation uses templates in output/responses.py for determinism.
"""

ACCOUNT_ID_EXTRACTION = (
    "You are an information extractor for a payment collection agent.\n"
    "Extract the account ID from the user's message.\n\n"
    "RULES:\n"
    "1. Only extract what the user EXPLICITLY stated. Do not guess.\n"
    "2. Account IDs follow the pattern ACC + digits (e.g. ACC1001). Normalize: strip spaces/hyphens, uppercase.\n"
    '3. If the user clearly provided an account ID, set account_id and user_intent = "provided_id".\n'
    '4. If the user is asking a question without providing an ID, set user_intent = "asking_question".\n'
    '5. If the user says "stop", "cancel", "quit", "end", set user_intent = "wants_to_cancel".\n\n'
    "EXAMPLES:\n"
    '"yeah my account number is ACC1001 I think" → {{"account_id": "ACC1001", "user_intent": "provided_id"}}\n'
    '"it\'s ACC 1001" → {{"account_id": "ACC1001", "user_intent": "provided_id"}}\n'
    '"account id: acc1001" → {{"account_id": "ACC1001", "user_intent": "provided_id"}}\n'
    '"I\'m not sure what my account ID is" → {{"account_id": null, "user_intent": "asking_question"}}\n'
    '"cancel" → {{"account_id": null, "user_intent": "wants_to_cancel"}}\n\n'
    "User's message: {user_input}"
)

IDENTITY_EXTRACTION = (
    "You are an information extractor for a payment collection agent.\n"
    "Extract identity fields from the user's message.\n\n"
    "CONTEXT — already collected:\n"
    "{already_collected}\n\n"
    "RULES:\n"
    "1. Extract every identity field the user explicitly stated, even if the message ALSO "
    "contains unrelated information like an account ID, a payment amount, a greeting, or "
    "a question. Do not skip identity fields just because the message's primary topic "
    "is something else — the user may front-load everything in one message.\n"
    "2. For names: strip common honorifics (Mr., Mrs., Ms., Dr., Sir, Madam, Shri, Smt., Ji) "
    "from the extracted name. Preserve original capitalization of the name itself. Trim whitespace. "
    "Ignore filler words like 'sir', 'actually', 'wait no' around the name.\n"
    "3. For DOB: only set dob if you are certain of day, month AND year. Convert verbal dates "
    "(e.g. 'fourteenth may nineteen ninety' → 1990-05-14). If the format could be "
    "DD-MM or MM-DD (e.g. '01-02-1990'), set dob = null and dob_ambiguous = true.\n"
    "4. For Aadhaar: extract only the last 4 digits. If user states their full 12-digit Aadhaar, "
    "extract only the last 4 — NEVER output the full number. Convert verbal digits "
    "('nine eight seven six' → '9876').\n"
    "5. For pincode: must be exactly 6 digits. '4 0 0 0 0 1' → '400001'.\n"
    "6. The user may mix Hindi/English (Hinglish). Understand the meaning and extract the field.\n"
    "7. user_intent: classify the primary purpose of their message.\n"
    '8. If user says "stop", "cancel", "quit", "end" → user_intent = "wants_to_cancel".\n\n'
    "EXAMPLES:\n"
    '"my name is Nithin Jain and DOB 14th May 1990"\n'
    '→ {{"full_name": "Nithin Jain", "dob": "1990-05-14", "dob_ambiguous": false, '
    '"aadhaar_last4": null, "pincode": null, "user_intent": "providing_info"}}\n\n'
    "// Compound first-turn message — extract identity fields even though the "
    "// message also contains account ID, payment intent, and a greeting:\n"
    '"Hi, my account is ACC1001, name Nithin Jain, DOB 14th May 1990, I want to pay 400 rupees"\n'
    '→ {{"full_name": "Nithin Jain", "dob": "1990-05-14", "dob_ambiguous": false, '
    '"aadhaar_last4": null, "pincode": null, "user_intent": "providing_info"}}\n\n'
    '"ACC1002 here, Rajarajeswari Balasubramaniam, aadhaar ends 9876"\n'
    '→ {{"full_name": "Rajarajeswari Balasubramaniam", "dob": null, "dob_ambiguous": false, '
    '"aadhaar_last4": "9876", "pincode": null, "user_intent": "providing_info"}}\n\n'
    '"Mr. Rahul Mehta here"\n'
    '→ {{"full_name": "Rahul Mehta", "dob": null, "dob_ambiguous": false, '
    '"aadhaar_last4": null, "pincode": null, "user_intent": "providing_info"}}\n\n'
    '"naam Nithin Jain hai"\n'
    '→ {{"full_name": "Nithin Jain", "dob": null, "dob_ambiguous": false, '
    '"aadhaar_last4": null, "pincode": null, "user_intent": "providing_info"}}\n\n'
    '"born on fourteenth may nineteen ninety"\n'
    '→ {{"full_name": null, "dob": "1990-05-14", "dob_ambiguous": false, '
    '"aadhaar_last4": null, "pincode": null, "user_intent": "providing_info"}}\n\n'
    '"I was born 01-02-90"\n'
    '→ {{"full_name": null, "dob": null, "dob_ambiguous": true, '
    '"aadhaar_last4": null, "pincode": null, "user_intent": "providing_info"}}\n\n'
    '"you can call me Raja but my full name is Rajarajeswari Balasubramaniam"\n'
    '→ {{"full_name": "Rajarajeswari Balasubramaniam", "dob": null, "dob_ambiguous": false, '
    '"aadhaar_last4": null, "pincode": null, "user_intent": "providing_info"}}\n\n'
    '"aadhaar last four is nine eight seven six"\n'
    '→ {{"full_name": null, "dob": null, "dob_ambiguous": false, '
    '"aadhaar_last4": "9876", "pincode": null, "user_intent": "providing_info"}}\n\n'
    '"my Aadhaar number is 1234 5678 4321"\n'
    '→ {{"full_name": null, "dob": null, "dob_ambiguous": false, '
    '"aadhaar_last4": "4321", "pincode": null, "user_intent": "providing_info"}}\n\n'
    '"pincode? it\'s 4 0 0 0 0 1"\n'
    '→ {{"full_name": null, "dob": null, "dob_ambiguous": false, '
    '"aadhaar_last4": null, "pincode": "400001", "user_intent": "providing_info"}}\n\n'
    "User's message: {user_input}"
)

DOB_CONFIRMATION = (
    "You are an information extractor for a payment collection agent.\n"
    "The agent presented a date to the user for confirmation.\n\n"
    "Presented date: {presented_date}\n\n"
    "Determine if the user confirmed or denied this date.\n\n"
    "RULES:\n"
    '1. "yes", "correct", "that\'s right", "yep", "yeah", "right" → confirmed = true, user_intent = "confirmed"\n'
    '2. "no", "wrong", "incorrect", "that\'s not right" → confirmed = false, user_intent = "denied"\n'
    '3. "stop", "cancel" → user_intent = "wants_to_cancel"\n'
    "4. Anything unclear → user_intent = \"unclear\"\n\n"
    "User's message: {user_input}"
)

AMOUNT_EXTRACTION = (
    "You are an information extractor for a payment collection agent.\n"
    "Extract the payment amount from the user's message.\n\n"
    "Outstanding balance: ₹{balance}\n\n"
    "RULES:\n"
    "1. Only extract what the user explicitly stated.\n"
    '2. Convert word-based amounts: "a thousand rupees" → 1000, "five hundred" → 500.\n'
    '3. If the user says "full amount", "clear it all", "pay everything", "pay the balance" → '
    "set wants_full_balance = true, amount = null.\n"
    "4. Do NOT set wants_full_balance if the stated amount happens to equal the balance — "
    "only set it if the user expressed intent to pay the full balance.\n"
    '5. "stop", "cancel" → user_intent = "wants_to_cancel".\n\n'
    "EXAMPLES:\n"
    '"I want to pay a thousand rupees" → {{"amount": 1000.00, "wants_full_balance": false, "user_intent": "providing_amount"}}\n'
    '"just clear the full amount" → {{"amount": null, "wants_full_balance": true, "user_intent": "providing_amount"}}\n'
    '"can I do 500 for now?" → {{"amount": 500.00, "wants_full_balance": false, "user_intent": "providing_amount"}}\n\n'
    "User's message: {user_input}"
)

CARD_EXTRACTION = (
    "You are an information extractor for a payment collection agent.\n"
    "Extract card payment details from the user's message.\n\n"
    "CONTEXT — already collected:\n"
    "{already_collected}\n\n"
    "RULES:\n"
    "1. Only extract what the user EXPLICITLY stated. Do not fill in from memory.\n"
    '2. card_number: digits only, no spaces. "4532 0151 1283 0366" → "4532015112830366".\n'
    '3. cvv: digits only. "one two three" → "123".\n'
    "4. expiry_month: integer 1-12. \"December\" → 12.\n"
    '5. expiry_year: 4-digit integer. "27" → 2027, "12/27" → month=12, year=2027.\n'
    "6. cardholder_name: as stated by the user.\n"
    '7. "stop", "cancel" → user_intent = "wants_to_cancel".\n\n'
    "EXAMPLES:\n"
    '"the card number is 4532 0151 1283 0366"\n'
    '→ {{"card_number": "4532015112830366", "cvv": null, "expiry_month": null, '
    '"expiry_year": null, "cardholder_name": null, "user_intent": "providing_card"}}\n\n'
    '"expires December 2027, CVV is one two three, cardholder Nithin Jain"\n'
    '→ {{"card_number": null, "cvv": "123", "expiry_month": 12, '
    '"expiry_year": 2027, "cardholder_name": "Nithin Jain", "user_intent": "providing_card"}}\n\n'
    '"4532015112830366, expires 12/27, CVV 123, cardholder Nithin Jain"\n'
    '→ {{"card_number": "4532015112830366", "cvv": "123", "expiry_month": 12, '
    '"expiry_year": 2027, "cardholder_name": "Nithin Jain", "user_intent": "providing_card"}}\n\n'
    "User's message: {user_input}"
)
