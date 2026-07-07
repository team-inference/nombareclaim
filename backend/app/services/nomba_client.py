"""
Thin wrapper around the Nomba API.

CORRECTION — this file previously treated Nomba's training-
certification material (training.nomba.com, downloaded PDF) as the
confirmed source for these API shapes. Nomba's real, current, official
developer docs (developer.nomba.com) were located afterward and are
now treated as authoritative wherever the two disagree — a live API
reference beats a training quiz, however official-sounding the quiz
seemed at the time. Several things below were corrected as a result:

1. THE SANDBOX HOSTNAME ITSELF WAS WRONG. The training material's
   `https://sandbox.api.nomba.com/v1` is not a real domain — it does
   not resolve via DNS at all (confirmed against two independent
   resolvers). The real sandbox host, per developer.nomba.com, is
   `https://sandbox.nomba.com` (no `api.` subdomain). Production is
   `https://api.nomba.com`, which was already correct.

2. SANDBOX CHECKOUT ENDPOINTS LIVE UNDER A DIFFERENT PATH PREFIX,
   `/sandbox/checkout/...`, NOT `/v1/checkout/...`. This isn't just a
   different host — the sandbox and production APIs use genuinely
   different paths for checkout-specific operations. Auth
   (`/v1/auth/token/issue`) is NOT affected — it's the same `/v1`
   prefix in both environments. This client now branches on whether
   NOMBA_API_BASE_URL contains "sandbox" to pick the right prefix —
   see _checkout_path_prefix() below.

3. CHECKOUT ORDER RESPONSE FIELD IS `checkoutLink`, NOT `checkoutUrl`.
   The training material's claim that it was confirmed as `checkoutUrl`
   was itself wrong; the official doc's real example response shows
   `data.checkoutLink`. Both are now accepted defensively (checkoutLink
   preferred), in case production ever differs from the sandbox
   example shown.

4. THE TRANSACTION-VERIFICATION ENDPOINT IS DIFFERENT THAN PREVIOUSLY
   CODED. Previously: `GET {base}/checkout/order/{orderReference}`
   (an unconfirmed guess). Actually, per the official sandbox-testing
   doc: `GET /sandbox/checkout/transaction?idType=orderReference&id=
   {ref}` in sandbox (production's exact query-param behavior at
   `/v1/checkout/transaction` is inferred from the difference table,
   not shown with its own full example — worth confirming against a
   real production transaction if this ever goes live).

5. Checkout amounts: still sent as INTEGER KOBO here (the
   training-quiz convention), NOT changed to match the official
   sandbox-testing doc's example, which oddly shows a DECIMAL STRING
   ("400000.00") for the same value. This is a genuine, unresolved
   conflict between two real doc sources on wire format for the
   REQUEST body specifically (separate from the webhook AMOUNT UNIT
   conflict already handled in routes/webhooks.py's
   _resolve_amount_naira). Kept as integer kobo since that's what
   this client already sends successfully-shaped requests with; if a
   real sandbox checkout creation call fails with a format complaint,
   try the decimal-string-kobo variant next, in that order.

Confirmed shapes (now cross-checked against the official
developer.nomba.com sandbox-testing doc):

- Auth: POST {base}/v1/auth/token/issue, header `accountId: <parent
  accountId>`, body {"grant_type": "client_credentials", "client_id":
  ..., "client_secret": ...}. Returns `data.access_token`.

- Checkout order creation: POST {base}/sandbox/checkout/order
  (sandbox) or {base}/v1/checkout/order (production), headers
  Authorization (bearer token) + accountId + Content-Type. Response:
  `data.checkoutLink` (primary) / `data.checkoutUrl` (fallback).

  **accountId scoping, per Nomba's own credentials brief for this
  hackathon** ("Authenticate with the parent Account ID in the
  accountId header, then scope your calls to your sub-account ID."):
  token issuance (_get_access_token) uses the PARENT accountId
  (NOMBA_ACCOUNT_ID); every call made AFTER auth — checkout order
  creation, status lookup — uses the SUB-ACCOUNT accountId
  (NOMBA_SUBACCOUNT_ID) via _auth_headers().

- Transaction verification: GET {base}/sandbox/checkout/transaction?
  idType=orderReference&id={order_reference} (sandbox) or
  {base}/v1/checkout/transaction?idType=orderReference&id={ref}
  (production, inferred). Response: `data.success` (bool),
  `data.message` / `data.transactionDetails.statusCode` (text status).
"""
import time
from datetime import datetime
from typing import Optional

import httpx

from app.config import settings


def _parse_expires_at(value) -> Optional[float]:
    """
    CORRECTED: Nomba's real token-issue response uses an absolute ISO
    8601 timestamp field `expiresAt` (e.g. "2026-07-07T15:30:00Z"),
    not a duration-in-seconds `expires_in` field. The previous version
    of this client only ever looked for `expires_in`, never found it,
    and silently always fell back to the hardcoded 3600s assumption.
    Harmless today since ~60min matches the documented token
    lifetime, but it means the real field was never actually being
    read. Returns a unix timestamp, or None if `value` is missing/
    unparseable so the caller can fall back safely.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


class NombaAPIError(Exception):
    pass


def _root_host() -> str:
    """NOMBA_API_BASE_URL is stored WITH a trailing /v1 (e.g.
    "https://sandbox.nomba.com/v1"), matching the auth endpoint's
    path. Checkout-specific sandbox endpoints hang directly off the
    root host instead (/sandbox/checkout/...), so this strips /v1
    back off when building those URLs."""
    base = settings.NOMBA_API_BASE_URL.rstrip("/")
    if base.endswith("/v1"):
        return base[: -len("/v1")]
    return base


def _is_sandbox() -> bool:
    return "sandbox" in settings.NOMBA_API_BASE_URL.lower()


def _checkout_path_prefix() -> str:
    """/sandbox/checkout in sandbox, /v1/checkout in production — a
    genuinely different path structure per environment, not just a
    different host. See module docstring point 2."""
    return "/sandbox/checkout" if _is_sandbox() else "/v1/checkout"


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

    # Prefer the real `expiresAt` (absolute ISO timestamp) field if
    # present; fall back to the documented ~60-minute assumption
    # (`expires_in`, or 3600s) only if it's missing/unparseable.
    expires_at_raw = data.get("expiresAt") or data.get("data", {}).get("expiresAt")
    parsed_expiry = _parse_expires_at(expires_at_raw)

    _token_cache.token = token
    if parsed_expiry is not None:
        _token_cache.expires_at = parsed_expiry
    else:
        expires_in = data.get("expires_in") or data.get("data", {}).get("expires_in") or 3600
        _token_cache.expires_at = time.time() + float(expires_in)
    return token


async def _auth_headers() -> dict:
    token = await _get_access_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "accountId": settings.NOMBA_SUBACCOUNT_ID,
    }


def _naira_to_kobo(amount_naira: int) -> str:
    """
    CORRECTED against a real worked example in Nomba's sandbox-testing
    guide: a checkout order created with "amount": "400000.00" comes
    back with order.amount / transaction.transactionAmount of
    4000.00 — exactly divided by 100, confirming the create-order
    amount is in KOBO. But the example sends it as a DECIMAL STRING,
    not a bare integer, which is the part this client previously got
    wrong. Returns e.g. "250000.00" for ₦2,500, not the int 250000.
    """
    kobo = round(amount_naira * 100)
    return f"{kobo:.2f}"


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
    converted to integer kobo here before sending. Callers elsewhere
    in this codebase never need to think about kobo.

    URL is environment-aware: /sandbox/checkout/order in sandbox,
    /v1/checkout/order in production — see module docstring point 2.
    """
    url = f"{_root_host()}{_checkout_path_prefix()}/order"
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
    # checkoutLink is the confirmed field per the official
    # sandbox-testing doc; checkoutUrl kept as a fallback only.
    checkout_url = data.get("checkoutLink") or data.get("checkoutUrl")
    if not checkout_url:
        raise NombaAPIError(f"checkout order response missing checkoutLink: {payload}")

    return {
        "checkout_url": checkout_url,
        "order_reference": data.get("orderReference", customer_reference),
    }


async def get_checkout_order_status(order_reference: str) -> dict:
    """
    Server-side verification lookup. NEVER trust a webhook payload
    alone to mark something RECOVERED — always cross-check against
    this before flipping status. See services/recovery.py.

    URL and query params are environment-aware, per the official
    sandbox-testing doc: GET /sandbox/checkout/transaction?
    idType=orderReference&id={ref} in sandbox. Production's exact
    query-param behavior at /v1/checkout/transaction is inferred from
    the doc's sandbox-vs-production difference table, not shown with
    its own full example — worth confirming against a real production
    transaction before this ever handles real money.
    """
    url = f"{_root_host()}{_checkout_path_prefix()}/transaction"
    params = {"idType": "orderReference", "id": order_reference}
    headers = await _auth_headers()
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=headers, params=params)
    if resp.status_code == 404:
        return {}
    if resp.status_code >= 400:
        raise NombaAPIError(f"checkout order lookup failed: {resp.status_code} {resp.text}")

    payload = resp.json()
    return payload.get("data", {})
