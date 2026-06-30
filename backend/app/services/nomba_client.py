"""
Thin wrapper around the Nomba API.

Grounded against developer.nomba.com/nomba-api-reference (fetched live
during this build, not assumed):

- Auth: POST {base}/auth/token/issue, header `accountId: <parent accountId>`,
  body {"grant_type": "client_credentials", "client_id": ..., "client_secret": ...}
  (Nomba's docs call the private key `client_secret` in this call).
  Returns an `access_token` JWT, used afterwards as `Authorization: Bearer <token>`.

- Checkout order creation: POST {base}/checkout/order, headers
  Authorization (bearer token) + accountId (parent), Content-Type: application/json.
  Body: {"order": {callbackUrl, customerEmail, amount (decimal STRING,
  e.g. "10000.00"), currency, orderReference, customerId, accountId
  (the SUB-account this order is scoped to), allowedPaymentMethods,
  orderMetaData}, "tokenizeCard": "false"}.
  Response: {"code": "00", "description": "Success",
  "data": {"checkoutLink": "...", "orderReference": "..."}}.

- Transaction lookup: the public reference pages found expose an
  account-wide list endpoint (GET {base}/transactions/accounts, header
  accountId) rather than a confirmed single-transaction-by-id GET. This
  client implements get_transaction_status by querying that list
  filtered to the merchant transaction reference and matching client-side.
  If Nomba's full reference (behind their dashboard) exposes a direct
  GET /transactions/{id}, swap that in here — it's a one-function change,
  nothing else in the codebase depends on the implementation detail.
"""
import time
from typing import Optional

import httpx

from app.config import settings


class NombaAPIError(Exception):
    pass


class _TokenCache:
    token: Optional[str] = None
    expires_at: float = 0.0


_token_cache = _TokenCache()


async def _get_access_token() -> str:
    # Cache the token in-process; refresh ~60s before real expiry.
    if _token_cache.token and time.time() < _token_cache.expires_at - 60:
        return _token_cache.token

    url = f"{settings.NOMBA_API_BASE_URL}/auth/token/issue"
    headers = {
        "Content-Type": "application/json",
        "accountId": settings.NOMBA_ACCOUNT_ID,
    }
    body = {
        "grant_type": "client_credentials",
        "client_id": settings.NOMBA_CLIENT_ID,
        "client_secret": settings.NOMBA_PRIVATE_KEY,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, headers=headers, json=body)
    if resp.status_code != 200:
        raise NombaAPIError(f"token issue failed: {resp.status_code} {resp.text}")

    data = resp.json()
    token = (
        data.get("access_token")
        or data.get("data", {}).get("access_token")
    )
    if not token:
        raise NombaAPIError(f"token issue response missing access_token: {data}")

    # Nomba's docs show a JWT with an `exp` claim but don't guarantee an
    # `expires_in` field in the response body; default to a conservative
    # 10-minute cache if not provided.
    expires_in = data.get("expires_in") or data.get("data", {}).get("expires_in") or 600
    _token_cache.token = token
    _token_cache.expires_at = time.time() + float(expires_in)
    return token


async def _auth_headers() -> dict:
    token = await _get_access_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "accountId": settings.NOMBA_ACCOUNT_ID,
    }


async def create_checkout_order(
    amount: int,
    currency: str,
    customer_reference: str,
    description: str,
    callback_url: str,
    customer_email: Optional[str] = None,
) -> dict:
    """
    amount is the integer naira amount as stored on FailureEvent; Nomba
    expects a decimal string with 2 places (e.g. "15000.00").
    """
    url = f"{settings.NOMBA_API_BASE_URL}/checkout/order"
    headers = await _auth_headers()
    body = {
        "order": {
            "callbackUrl": callback_url,
            "customerEmail": customer_email or "customer@example.com",
            "amount": f"{amount:.2f}",
            "currency": currency,
            "orderReference": customer_reference,
            "customerId": customer_reference,
            "accountId": settings.NOMBA_SUBACCOUNT_ID,
            "allowedPaymentMethods": ["Card", "Transfer"],
            "orderMetaData": {
                "productName": "NombaReclaim recovery checkout",
                "internalRef": customer_reference,
                "description": description,
            },
        },
        "tokenizeCard": "false",
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(url, headers=headers, json=body)
    if resp.status_code >= 400:
        raise NombaAPIError(f"checkout order creation failed: {resp.status_code} {resp.text}")

    payload = resp.json()
    data = payload.get("data", {})
    if not data.get("checkoutLink"):
        raise NombaAPIError(f"checkout order response missing checkoutLink: {payload}")

    return {
        "checkout_url": data["checkoutLink"],
        "order_reference": data.get("orderReference", customer_reference),
    }


async def get_transaction_status(transaction_reference: str) -> dict:
    """
    Server-side verification lookup. NEVER trust a webhook payload alone
    to mark something RECOVERED — always cross-check against this before
    flipping status. See services/recovery.py / routes/webhooks.py.
    """
    url = f"{settings.NOMBA_API_BASE_URL}/transactions/accounts"
    headers = await _auth_headers()
    params = {"merchantTxRef": transaction_reference, "limit": 5}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=headers, params=params)
    if resp.status_code >= 400:
        raise NombaAPIError(f"transaction lookup failed: {resp.status_code} {resp.text}")

    payload = resp.json()
    results = payload.get("data", {}).get("results", [])
    for tx in results:
        if tx.get("merchantTxRef") == transaction_reference:
            return tx
    return {}
