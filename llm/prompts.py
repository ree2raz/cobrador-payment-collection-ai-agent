"""
Extraction prompt templates — one per state that needs LLM parsing.
Response generation uses templates in output/responses.py for determinism.
"""

ACCOUNT_ID_EXTRACTION = (
    "You are an information extractor for a payment collection agent.\n"
    "Extract the account ID from the user's message.\n\n"
    "RULES:\n"
    "1. Only extract what the user EXPLICITLY stated. Do not guess.\n"
    "2. Account IDs follow the pattern ACC + digits (e.g. ACC1001). Normalize: "
    "strip spaces/hyphens, uppercase.\n"
    "3. The message may also contain unrelated information (name, DOB, payment "
    "intent, greeting). Extract the account ID anyway.\n"
    '4. If the user clearly provided an account ID, set account_id and user_intent = "provided_id".\n'
    '5. If the user is asking a question without providing an ID, set user_intent = "asking_question".\n'
    '6. If the user says "stop", "cancel", "quit", "end", set user_intent = "wants_to_cancel".\n\n'
    "EXAMPLES:\n"
    '"my account number is ACC1001" → {{"account_id": "ACC1001", "user_intent": "provided_id"}}\n'
    '"it\'s ACC 1001" → {{"account_id": "ACC1001", "user_intent": "provided_id"}}\n'
    '"account id: acc1001" → {{"account_id": "ACC1001", "user_intent": "provided_id"}}\n'
    '"Hi I am Nithin Jain, my account id is acc1001, pay 400" '
    '→ {{"account_id": "ACC1001", "user_intent": "provided_id"}}\n'
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
    "contains unrelated information like an account ID, a payment amount, or a greeting.\n"
    "2. NAME CASING (CRITICAL): the extracted full_name MUST be Title-Case "
    "(each word capitalized: 'Rahul Mehta', not 'rahul mehta' or 'RAHUL MEHTA'). "
    "Users often type their names in lowercase or all-caps; you MUST convert to "
    "Title-Case before returning. Verification is strict case-sensitive, so a "
    "lowercase return value will cause incorrect verification failure. "
    "Examples: 'i am nithin jain' → 'Nithin Jain'; 'RAHUL MEHTA HERE' → "
    "'Rahul Mehta'; 'priya agarwal' → 'Priya Agarwal'.\n"
    "3. For names: strip common honorifics (Mr., Mrs., Ms., Dr., Sir, Madam) from the "
    "extracted name. Trim whitespace. If the user gives a nickname and a full name "
    "(e.g. 'call me Raja but my full name is Rajarajeswari Balasubramaniam'), extract "
    "the FULL name, not the nickname.\n"
    "4. For DOB: only set dob if you are certain of day, month AND year. If the format "
    "could be DD-MM or MM-DD (e.g. '01-02-1990'), set dob = null and dob_ambiguous = true.\n"
    "5. For Aadhaar: extract only the last 4 digits. If user states their full 12-digit "
    "Aadhaar, extract only the last 4 — NEVER output the full number.\n"
    "6. For pincode: must be exactly 6 digits.\n"
    "7. user_intent: classify the primary purpose of their message.\n"
    '8. If user says "stop", "cancel", "quit", "end" → user_intent = "wants_to_cancel".\n\n'
    "EXAMPLES:\n"
    '"my name is Nithin Jain and DOB 14th May 1990"\n'
    '→ {{"full_name": "Nithin Jain", "dob": "1990-05-14", "dob_ambiguous": false, '
    '"aadhaar_last4": null, "pincode": null, "user_intent": "providing_info"}}\n\n'
    '"Hi i am nithin jain, my account id is acc1001, my dob is 14 may 1990"\n'
    '→ {{"full_name": "Nithin Jain", "dob": "1990-05-14", "dob_ambiguous": false, '
    '"aadhaar_last4": null, "pincode": null, "user_intent": "providing_info"}}\n\n'
    "// Lowercase-name input — MUST be returned Title-Cased:\n"
    '"hi i am rahul mehta i want to pay 3500"\n'
    '→ {{"full_name": "Rahul Mehta", "dob": null, "dob_ambiguous": false, '
    '"aadhaar_last4": null, "pincode": null, "user_intent": "providing_info"}}\n\n'
    '"Mr. Rahul Mehta here, aadhaar ends 9876"\n'
    '→ {{"full_name": "Rahul Mehta", "dob": null, "dob_ambiguous": false, '
    '"aadhaar_last4": "9876", "pincode": null, "user_intent": "providing_info"}}\n\n'
    "// Nickname + full name — always pick the full name:\n"
    '"you can call me Raja but my full name is Rajarajeswari Balasubramaniam"\n'
    '→ {{"full_name": "Rajarajeswari Balasubramaniam", "dob": null, "dob_ambiguous": false, '
    '"aadhaar_last4": null, "pincode": null, "user_intent": "providing_info"}}\n\n'
    '"I was born 01-02-90"\n'
    '→ {{"full_name": null, "dob": null, "dob_ambiguous": true, '
    '"aadhaar_last4": null, "pincode": null, "user_intent": "providing_info"}}\n\n'
    '"my Aadhaar number is 1234 5678 4321"\n'
    '→ {{"full_name": null, "dob": null, "dob_ambiguous": false, '
    '"aadhaar_last4": "4321", "pincode": null, "user_intent": "providing_info"}}\n\n'
    '"pincode 400001"\n'
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
    '3. cvv: digits only. Convert verbal digits to numerals: "one two three" → "123".\n'
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
    '"expiry_year": 2027, "cardholder_name": "Nithin Jain", "user_intent": "providing_card"}}\n'
    "// Verbal CVV example (brief's exact phrasing):\n"
    '"CVV is one two three" → {{"card_number": null, "cvv": "123", "expiry_month": null, '
    '"expiry_year": null, "cardholder_name": null, "user_intent": "providing_card"}}\n\n'
    '"4532015112830366, expires 12/27, CVV 123, cardholder Nithin Jain"\n'
    '→ {{"card_number": "4532015112830366", "cvv": "123", "expiry_month": 12, '
    '"expiry_year": 2027, "cardholder_name": "Nithin Jain", "user_intent": "providing_card"}}\n\n'
    "User's message: {user_input}"
)
