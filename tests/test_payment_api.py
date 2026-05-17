from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
from tenacity import wait_none

from core.state_machine import CardDetails
from tools import payment_api


def _client_factory_for_statuses(statuses: list[tuple[int, dict | str]]):
    requests: list[httpx.Request] = []
    remaining = list(statuses)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        status, payload = remaining.pop(0)
        if isinstance(payload, str):
            return httpx.Response(status, text=payload)
        return httpx.Response(status, json=payload)

    transport = httpx.MockTransport(handler)
    return lambda: httpx.Client(transport=transport), requests


def test_process_payment_retries_server_error_and_sends_expected_payload(monkeypatch):
    client_factory, requests = _client_factory_for_statuses([
        (500, {"error": "temporary"}),
        (200, {"success": True, "transaction_id": "txn_TEST123"}),
    ])
    monkeypatch.setattr(payment_api, "_make_client", client_factory)
    monkeypatch.setattr(
        payment_api,
        "_process_payment_request",
        payment_api._process_payment_request.retry_with(wait=wait_none()),
    )

    result = payment_api.process_payment(
        "ACC1001",
        Decimal("500.00"),
        CardDetails(
            card_number="4532015112830366",
            cvv="123",
            expiry_month=12,
            expiry_year=2027,
            cardholder_name="Nithin Jain",
        ),
    )

    assert result.success is True
    assert result.transaction_id == "txn_TEST123"
    assert len(requests) == 2
    payload = requests[-1].read()
    assert b'"account_id":"ACC1001"' in payload
    assert b'"amount":500.0' in payload
    assert b'"card_number":"4532015112830366"' in payload
    assert b'"cvv":"123"' in payload


def test_lookup_malformed_json_retries(monkeypatch):
    client_factory, requests = _client_factory_for_statuses([
        (200, "{not-json"),
        (200, {
            "account_id": "ACC1001",
            "full_name": "Nithin Jain",
            "dob": "1990-05-14",
            "aadhaar_last4": "4321",
            "pincode": "400001",
            "balance": 1250.75,
        }),
    ])
    monkeypatch.setattr(payment_api, "_make_client", client_factory)
    retrying_lookup = payment_api.lookup_account.retry_with(wait=wait_none())

    result = retrying_lookup("ACC1001")

    assert result.success is True
    assert result.account is not None
    assert result.account.dob == date(1990, 5, 14)
    assert len(requests) == 2
