from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel


class SummaryResponse(BaseModel):
    total_failed_count: int
    total_failed_amount: int
    estimated_recoverable_amount: int
    recovered_amount: int
    recovery_rate: float
    currency: str = "NGN"
    period: str = "month"


class FailureItem(BaseModel):
    id: str
    nomba_transaction_id: str
    amount: int
    currency: str
    classification: Optional[str] = None
    recovery_score: Optional[int] = None
    status: str
    recovery_message: Optional[str] = None
    created_at: datetime
    recovered_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class FailureDetail(FailureItem):
    recovery_checkout_url: Optional[str] = None


class FailureListResponse(BaseModel):
    results: List[FailureItem]
    total: int
    page: int
    page_size: int


class TriggerRecoveryResponse(BaseModel):
    id: str
    status: str
    recovery_checkout_url: Optional[str] = None
    triggered_at: datetime


class HealthResponse(BaseModel):
    status: str = "ok"
