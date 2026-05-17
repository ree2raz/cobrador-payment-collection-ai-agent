from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from json import JSONDecodeError
from typing import Optional

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from core.state_machine import AccountRecord, CardDetails

logger = logging.getLogger(__name__)

BASE_URL = "https://se-payment-verification-api.service.external.usea2.aws.prodigaltech.com"
TIMEOUT = 10.0


@dataclass
class LookupResult:
    success: bool
    account: Optional[AccountRecord] = None
    error_code: Optional[str] = None  # "account_not_found" | "server_error"


@dataclass
class PaymentResult:
    success: bool
    transaction_id: Optional[str] = None
    error_code: Optional[str] = None


class ServerError(Exception):
    pass


def _make_client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT)


@retry(
    retry=retry_if_exception_type(ServerError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    reraise=True,
)
def lookup_account(account_id: str) -> LookupResult:
    try:
        with _make_client() as client:
            resp = client.post(
                f"{BASE_URL}/api/lookup-account",
                json={"account_id": account_id},
            )
    except httpx.TimeoutException as e:
        logger.warning("lookup_account timeout for %s", account_id)
        raise ServerError("timeout") from e
    except httpx.RequestError as e:
        logger.warning("lookup_account network error for %s: %s", account_id, e)
        raise ServerError("network_error") from e

    if resp.status_code == 200:
        try:
            data = resp.json()
            account = AccountRecord(
                account_id=data["account_id"],
                full_name=data["full_name"],
                dob=date.fromisoformat(data["dob"]),
                aadhaar_last4=data["aadhaar_last4"],
                pincode=data["pincode"],
                balance=Decimal(str(data["balance"])),
            )
        except (JSONDecodeError, KeyError, TypeError, ValueError) as e:
            logger.warning("lookup_account malformed response account_id=%s", account_id)
            raise ServerError("malformed_response") from e
        logger.info("lookup_account success account_id=%s", account_id)
        return LookupResult(success=True, account=account)

    if resp.status_code == 404:
        logger.info("lookup_account not_found account_id=%s", account_id)
        return LookupResult(success=False, error_code="account_not_found")

    # 5xx or unexpected
    logger.warning("lookup_account server_error status=%d account_id=%s", resp.status_code, account_id)
    raise ServerError(f"HTTP {resp.status_code}")


def process_payment(
    account_id: str,
    amount: Decimal,
    card: CardDetails,
) -> PaymentResult:
    payload = {
        "account_id": account_id,
        "amount": float(amount),
        "payment_method": {
            "type": "card",
            "card": {
                "cardholder_name": card.cardholder_name,
                "card_number": card.card_number,
                "cvv": card.cvv,
                "expiry_month": card.expiry_month,
                "expiry_year": card.expiry_year,
            },
        },
    }

    return _process_payment_request(account_id, payload)


@retry(
    retry=retry_if_exception_type(ServerError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    reraise=True,
)
def _process_payment_request(account_id: str, payload: dict) -> PaymentResult:
    try:
        with _make_client() as client:
            resp = client.post(f"{BASE_URL}/api/process-payment", json=payload)
    except (httpx.TimeoutException, httpx.RequestError) as e:
        logger.warning("process_payment network error account_id=%s: %s", account_id, e)
        raise ServerError("network_error") from e

    if resp.status_code == 200:
        try:
            data = resp.json()
        except JSONDecodeError as e:
            logger.warning("process_payment malformed success account_id=%s", account_id)
            raise ServerError("malformed_response") from e
        txn_id = data.get("transaction_id")
        logger.info("process_payment success account_id=%s txn=%s", account_id, txn_id)
        return PaymentResult(success=True, transaction_id=txn_id)

    if resp.status_code == 422:
        try:
            data = resp.json()
        except JSONDecodeError as e:
            logger.warning("process_payment malformed 422 account_id=%s", account_id)
            raise ServerError("malformed_response") from e
        error_code = data.get("error_code", "unknown_error")
        logger.info("process_payment failure account_id=%s error=%s", account_id, error_code)
        return PaymentResult(success=False, error_code=error_code)

    logger.warning("process_payment unexpected status=%d account_id=%s", resp.status_code, account_id)
    raise ServerError(f"HTTP {resp.status_code}")
