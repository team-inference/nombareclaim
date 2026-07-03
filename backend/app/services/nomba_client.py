"""
Thin wrapper around the Nomba API.

Grounded against Nomba's own official developer certification training
material (training.nomba.com, downloaded PDF) — this replaces an
earlier version that had to guess several of these details from public
doc pages alone. Two things below were previously WRONG and are fixed
here:

1. Sandbox is a SEPARATE HOST, not the same host with different
   credentials: https://sandbox.api.nomba.com/v1 for all hackathon
   work, https://api.nomba.com/v1 only after KYC/production. An
   earlier version of this file incorrectly assumed one shared host.
2. Checkout amounts are in KOBO, as an INTEGER — not a decimal-string
   naira amount. ₦1.00 is 100 kobo; the training material's own
   example sends `amount: 250000` for a ₦2,500.00 charge. This system
   stores amounts internally in naira (matching the shared dashboard
   API contract, e.g. `"amount": 15000` meaning ₦15,000), so the
   naira<->kobo conversion happens at this module's boundary in both
   directions — nowhere else in the codebase should need to think
   about kobo at all.

Confirmed shapes:

- Auth: POST {base}/auth/token/issue, header `accountId: <parent
  accountId>`, body {"grant_type": "client_credentials", "client_id":
  ..., "client_secret": ...}. Returns `data.access_token`, valid 60
  minutes (this client refreshes early, well before that).

- Checkout order creation: POST {base}/checkout/order, headers
  Authorization (bearer token) + accountId (parent) + Content-Type.
  Body: {"order": {orderReference, amount (INTEGER KOBO), currency,
  callbackUrl, customerId, customerEmail}}. Response:
  `data.checkoutUrl` (NOT `checkoutLink` — an earlier version of this
  file had this field name wrong).

- Checkout order status lookup: GET {base}/checkout/order/{orderReference}
  — a direct, confirmed endpoint. This replaces an earlier guessed
  implementation that queried a `/transactions/accounts` list endpoint
  and filtered client-side; that guess is no longer needed now that a
  direct lookup is confirmed to exist.

Still open / not confirmed by any source seen so far, and worth
checking against a real sandbox test before the demo (see the
`docs/checkout/order` sub-account note below, and SECURITY.md):
- Whether checkout order creation needs an explicit sub-account
  `accountId` inside the order body at all — the confirmed training
  example does not include one. This client currently does NOT send
  one, matching the confirmed example exactly, rather than the earlier
  guessed version which invented one.
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
    # Cache the token in-process; refresh with a comfortable margin
    # before the confirmed 60-minute expiry (training material:
    # "Tokens are valid for 60 minutes... refresh at the 55-minute
    # mark"). This client refreshes 5 minutes early, matching that
    # guidance, rather than an earlier arbitrary 10-minute default.
    if _token_cache.token and time.time() < _token_cache.expires_at - 300:
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
    token = data.get("access_token") or data.get("data", {}).get("access_token")
    if not token:
        raise NombaAPIError(f"token issue response missing access_token: {data}")

    # Confirmed: tokens are valid 60 minutes. Fall back to that exact
    # figure if the response doesn't echo expires_in explicitly.
    expires_in = data.get("expires_in") or data.get("data", {}).get("expires_in") or 3600
    _token_cache.token = token
    _token_cache.expires_at = time.time() + float(expires_in)
    return token


async def _auth_headers() -> dict:
    token = await _get_access_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "accountId": settings.NOMBA_SUBACCOUNT_ID,
    }


def _naira_to_kobo(amount_naira: int) -> int:
    return round(amount_naira * 100)


def _kobo_to_naira(amount_kobo) -> int:
    """Accepts int, float, or numeric string — webhook payloads and API
    responses aren't guaranteed to send amount as the same JSON type
    every time, so this tolerates any of them."""
    return round(float(amount_kobo) / 100)


async def create_checkout_order(
    amount: int,
    currency: str,
    customer_reference: str,
    description: str,
    callback_url: str,
    customer_email: Optional[str] = None,
) -> dict:
    """
    amount is the integer NAIRA amount as stored on FailureEvent —
    converted to integer kobo here before sending, per Nomba's
    confirmed convention. Callers elsewhere in this codebase never
    need to think about kobo.
    """
    url = f"{settings.NOMBA_API_BASE_URL}/checkout/order"
    headers = await _auth_headers()
    body = {
        "order": {
            "orderReference": customer_reference,
            "amount": _naira_to_kobo(amount),
            "currency": currency,
            "callbackUrl": callback_url,
            "customerId": customer_reference,
            "customerEmail": customer_email or "customer@example.com",
        }
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(url, headers=headers, json=body)
    if resp.status_code >= 400:
        raise NombaAPIError(f"checkout order creation failed: {resp.status_code} {resp.text}")

    payload = resp.json()
    data = payload.get("data", {})
    checkout_url = data.get("checkoutUrl")
    if not checkout_url:
        raise NombaAPIError(f"checkout order response missing checkoutUrl: {payload}")

    return {
        "checkout_url": checkout_url,
        "order_reference": data.get("orderReference", customer_reference),
    }


async def get_checkout_order_status(order_reference: str) -> dict:
    """
    Server-side verification lookup, using the confirmed direct status
    endpoint. NEVER trust a webhook payload alone to mark something
    RECOVERED — always cross-check against this before flipping status.
    See services/recovery.py.
    """
    url = f"{settings.NOMBA_API_BASE_URL}/checkout/order/{order_reference}"
    headers = await _auth_headers()
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code == 404:
        return {}
    if resp.status_code >= 400:
        raise NombaAPIError(f"checkout order lookup failed: {resp.status_code} {resp.text}")

    payload = resp.json()
    return payload.get("data", {})
