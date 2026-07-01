"""
Classification engine.

Design (and the line to give judges if asked "how does the AI actually
decide"): classification of *why* a payment failed is deterministic
wherever Nomba's responseCode/transaction type give an unambiguous
signal — that's a dict lookup, not a model call, and it's instant and
free. AI is used for exactly two things: (1) breaking ties when the
response code is ambiguous or unrecognised, and (2) writing the
free-text recovery message, which is always model-generated because it
needs to read like a human wrote it, not like a templated string.

AI provider chain: Gemini is tried first, Groq second (only if Gemini
is unconfigured or its call fails for any reason), and a plain
deterministic template last. Nothing about webhook ingestion ever
blocks on or breaks from an AI call failing — every provider function
below catches its own exceptions and returns None rather than raising,
so classify_failure() can always fall through to the next option in
the chain and, worst case, land on the template. This is deliberate:
a flaky third-party AI API should never be able to take down the
webhook receiver.

The response-code mapping below is a best-effort table built from
common PSP/processor response code conventions (Nomba's own full
response-code dictionary was not in the public docs pages reachable
during this build). Before the July 3 checkpoint, replace this dict
with the real one from a logged-in Nomba dashboard view or from a
support request to Nomba — the code is structured so that's a one-place
edit (RESPONSE_CODE_MAP below), nothing else needs to change.
"""
import json
from dataclasses import dataclass
from typing import Optional

import httpx

from app.config import settings
from app.models import Classification

# --- Deterministic response-code -> classification mapping ---
# Keys are best-effort common ISO-8583-style processor codes. CONFIRM
# against Nomba's real values before relying on this for the demo.
RESPONSE_CODE_MAP = {
    "51": Classification.INSUFFICIENT_FUNDS,
    "61": Classification.INSUFFICIENT_FUNDS,
    "05": Classification.CARD_DECLINED,
    "14": Classification.CARD_DECLINED,
    "43": Classification.CARD_DECLINED,
    "57": Classification.CARD_DECLINED,
    "62": Classification.CARD_DECLINED,
    "91": Classification.NETWORK_TIMEOUT,
    "96": Classification.NETWORK_TIMEOUT,
    "68": Classification.NETWORK_TIMEOUT,
}

# Deterministic base score per classification (0-100). Tuned for "this is
# a reasonable, explainable starting point", not a trained model — say
# so plainly if asked. Amount is folded in afterwards as a mild modifier.
BASE_SCORE = {
    Classification.INSUFFICIENT_FUNDS: 70,
    Classification.CARD_DECLINED: 45,
    Classification.NETWORK_TIMEOUT: 80,
    Classification.USER_ABANDONED: 55,
    Classification.OTHER: 30,
}


@dataclass
class ClassificationResult:
    classification: Classification
    recovery_score: int
    recovery_message: str


def _deterministic_classification(
    response_code: Optional[str], transaction_type: Optional[str], event_type: str
) -> Optional[Classification]:
    if event_type == "payment_abandoned" or (transaction_type or "").lower() == "abandoned":
        return Classification.USER_ABANDONED
    if response_code and response_code in RESPONSE_CODE_MAP:
        return RESPONSE_CODE_MAP[response_code]
    return None


def _score_for(classification: Classification, amount: int) -> int:
    score = BASE_SCORE.get(classification, 30)
    # Small, explainable amount modifier: very small failed amounts are
    # slightly more likely to be casually abandoned/retried; very large
    # ones slightly less likely to be casually retried by the customer
    # without a nudge. Kept deliberately mild (+/- 10) so the response
    # code classification still dominates the score.
    if amount and amount < 2000:
        score += 5
    elif amount and amount > 200000:
        score -= 10
    return max(0, min(100, score))


def _fallback_message(classification: Classification, amount: int, currency: str) -> str:
    """Used if Gemini is unavailable (no key configured, or the call
    fails) so the pipeline degrades gracefully instead of breaking the
    webhook flow."""
    amount_str = f"{currency} {amount:,}"
    templates = {
        Classification.INSUFFICIENT_FUNDS: (
            f"Hi! Your payment of {amount_str} didn't go through — "
            f"looks like a funds issue. Here's a fresh link to complete it whenever you're ready."
        ),
        Classification.CARD_DECLINED: (
            f"Hi! Your card declined the {amount_str} payment. "
            f"You can try a different card or bank transfer here."
        ),
        Classification.NETWORK_TIMEOUT: (
            f"Hi! Your {amount_str} payment didn't complete due to a network hiccup on our end, "
            f"not yours. Please try again with this fresh link."
        ),
        Classification.USER_ABANDONED: (
            f"Hi! You started a {amount_str} payment but didn't finish — "
            f"still interested? Here's your checkout link."
        ),
        Classification.OTHER: (
            f"Hi! Your {amount_str} payment didn't go through. "
            f"Here's a fresh link to try again."
        ),
    }
    return templates[classification]


def _gemini_message(classification: Classification, amount: int, currency: str) -> Optional[str]:
    if not settings.GEMINI_API_KEY:
        return None
    try:
        import google.generativeai as genai

        genai.configure(api_key=settings.GEMINI_API_KEY)
        model = genai.GenerativeModel(settings.GEMINI_MODEL)
        prompt = _message_prompt(classification, amount, currency)
        response = model.generate_content(prompt)
        text = (response.text or "").strip()
        return text or None
    except Exception:
        # Never let a flaky AI call break webhook ingestion. Returning
        # None lets classify_failure() fall through to Groq, then the
        # deterministic template.
        return None


def _gemini_classify_ambiguous(
    response_code: Optional[str], transaction_type: Optional[str], raw_payload: dict
) -> Optional[Classification]:
    if not settings.GEMINI_API_KEY:
        return None
    try:
        import google.generativeai as genai

        genai.configure(api_key=settings.GEMINI_API_KEY)
        model = genai.GenerativeModel(settings.GEMINI_MODEL)
        prompt = _classify_prompt(response_code, transaction_type, raw_payload)
        response = model.generate_content(prompt)
        label = (response.text or "").strip().upper()
        return Classification[label] if label in Classification.__members__ else None
    except Exception:
        return None


def _message_prompt(classification: Classification, amount: int, currency: str) -> str:
    return (
        "Write a short, warm, Nigerian-English recovery message a merchant "
        "could send a customer whose payment just failed. 1-2 sentences. "
        "Plain text only, no markdown, no preamble, no quotation marks.\n"
        f"Failure reason: {classification.value}\n"
        f"Amount: {currency} {amount}\n"
        "Merchant name: [Merchant]\n"
    )


def _classify_prompt(response_code: Optional[str], transaction_type: Optional[str], raw_payload: dict) -> str:
    return (
        "Classify a failed Nigerian payment transaction into exactly one of: "
        "INSUFFICIENT_FUNDS, CARD_DECLINED, NETWORK_TIMEOUT, USER_ABANDONED, OTHER.\n"
        f"Response code: {response_code}\n"
        f"Transaction type: {transaction_type}\n"
        f"Raw payload (truncated): {json.dumps(raw_payload)[:1500]}\n"
        "Reply with ONLY the single classification label, nothing else."
    )


def _groq_chat(prompt: str) -> Optional[str]:
    """Shared Groq call: OpenAI-compatible chat completions endpoint.
    Uses plain httpx rather than an SDK, since it's a single endpoint
    and this avoids adding a new dependency for one call shape."""
    if not settings.GROQ_API_KEY:
        return None
    try:
        resp = httpx.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.4,
                "max_tokens": 120,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        text = data["choices"][0]["message"]["content"].strip()
        return text or None
    except Exception:
        # Same rule as the Gemini functions: swallow everything, return
        # None, let classify_failure() fall through to the template.
        return None


def _groq_message(classification: Classification, amount: int, currency: str) -> Optional[str]:
    return _groq_chat(_message_prompt(classification, amount, currency))


def _groq_classify_ambiguous(
    response_code: Optional[str], transaction_type: Optional[str], raw_payload: dict
) -> Optional[Classification]:
    text = _groq_chat(_classify_prompt(response_code, transaction_type, raw_payload))
    if not text:
        return None
    label = text.strip().upper()
    return Classification[label] if label in Classification.__members__ else None


def classify_failure(
    response_code: Optional[str],
    transaction_type: Optional[str],
    event_type: str,
    amount: int,
    currency: str,
    raw_payload: dict,
) -> ClassificationResult:
    classification = _deterministic_classification(response_code, transaction_type, event_type)

    if classification is None:
        classification = _gemini_classify_ambiguous(response_code, transaction_type, raw_payload)
    if classification is None:
        classification = _groq_classify_ambiguous(response_code, transaction_type, raw_payload)
    if classification is None:
        classification = Classification.OTHER

    score = _score_for(classification, amount)

    message = (
        _gemini_message(classification, amount, currency)
        or _groq_message(classification, amount, currency)
        or _fallback_message(classification, amount, currency)
    )

    return ClassificationResult(
        classification=classification,
        recovery_score=score,
        recovery_message=message,
    )
