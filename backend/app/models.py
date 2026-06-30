import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    String,
    Integer,
    Text,
    DateTime,
    Enum as SAEnum,
)

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Classification(str, enum.Enum):
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    CARD_DECLINED = "CARD_DECLINED"
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    USER_ABANDONED = "USER_ABANDONED"
    OTHER = "OTHER"


class FailureStatus(str, enum.Enum):
    NEW = "NEW"
    CLASSIFIED = "CLASSIFIED"
    RECOVERY_TRIGGERED = "RECOVERY_TRIGGERED"
    RECOVERED = "RECOVERED"
    EXPIRED = "EXPIRED"


class FailureEvent(Base):
    """
    One row per failed/abandoned Nomba payment event.

    `amount` is stored as an integer in NGN (naira), not kobo, to match
    the API contract examples in the shared brief (e.g. 15000 means
    fifteen thousand naira). Nomba's checkout order API takes amount as
    a decimal string (e.g. "15000.00") — conversion happens at the
    nomba_client boundary, not in storage.
    """

    __tablename__ = "failure_events"

    id = Column(String, primary_key=True, default=_uuid)

    nomba_transaction_id = Column(String, index=True, nullable=False)
    request_id = Column(String, nullable=True)
    merchant_user_id = Column(String, nullable=True)
    wallet_id = Column(String, nullable=True)
    event_type = Column(String, nullable=False)
    transaction_type = Column(String, nullable=True)

    amount = Column(Integer, nullable=False, default=0)
    currency = Column(String, nullable=False, default="NGN")
    response_code = Column(String, nullable=True)

    raw_payload = Column(Text, nullable=False)

    classification = Column(SAEnum(Classification), nullable=True)
    recovery_score = Column(Integer, nullable=True)
    recovery_message = Column(Text, nullable=True)

    status = Column(SAEnum(FailureStatus), nullable=False, default=FailureStatus.NEW)

    recovery_checkout_order_id = Column(String, nullable=True)
    recovery_checkout_url = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    recovered_at = Column(DateTime(timezone=True), nullable=True)

    idempotency_key = Column(String, unique=True, index=True, nullable=False)
