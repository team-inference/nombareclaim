from typing import Optional

from fastapi import APIRouter, Depends, Request, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import FailureEvent, FailureStatus
from app.schemas import (
    SummaryResponse,
    FailureListResponse,
    FailureDetail,
    TriggerRecoveryResponse,
)
from app.services.recovery import trigger_recovery, RecoveryError
from app.middleware.rate_limit import check_rate_limit
from app.config import settings

router = APIRouter(prefix="/api")


@router.get("/summary", response_model=SummaryResponse)
def get_summary(db: Session = Depends(get_db)):
    total_failed_count = db.query(func.count(FailureEvent.id)).scalar() or 0
    total_failed_amount = db.query(func.coalesce(func.sum(FailureEvent.amount), 0)).scalar() or 0

    recovered_amount = (
        db.query(func.coalesce(func.sum(FailureEvent.amount), 0))
        .filter(FailureEvent.status == FailureStatus.RECOVERED)
        .scalar()
        or 0
    )
    recovered_count = (
        db.query(func.count(FailureEvent.id))
        .filter(FailureEvent.status == FailureStatus.RECOVERED)
        .scalar()
        or 0
    )

    estimated_recoverable_amount = (
        db.query(func.coalesce(func.sum(FailureEvent.amount), 0))
        .filter(
            FailureEvent.recovery_score >= settings.RECOVERY_SCORE_THRESHOLD,
            FailureEvent.status != FailureStatus.RECOVERED,
        )
        .scalar()
        or 0
    )

    recovery_rate = (
        round((recovered_count / total_failed_count) * 100, 1) if total_failed_count else 0.0
    )

    return SummaryResponse(
        total_failed_count=total_failed_count,
        total_failed_amount=int(total_failed_amount),
        estimated_recoverable_amount=int(estimated_recoverable_amount),
        recovered_amount=int(recovered_amount),
        recovery_rate=recovery_rate,
        currency="NGN",
        period="month",
    )


@router.get("/failures", response_model=FailureListResponse)
def list_failures(
    status: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    q = db.query(FailureEvent)
    if status:
        q = q.filter(FailureEvent.status == status)

    total = q.count()
    items = (
        q.order_by(FailureEvent.recovery_score.desc().nullslast(), FailureEvent.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return FailureListResponse(
        results=[
            {
                "id": i.id,
                "nomba_transaction_id": i.nomba_transaction_id,
                "amount": i.amount,
                "currency": i.currency,
                "classification": i.classification.value if i.classification else None,
                "recovery_score": i.recovery_score,
                "status": i.status.value if hasattr(i.status, "value") else i.status,
                "recovery_message": i.recovery_message,
                "created_at": i.created_at,
                "recovered_at": i.recovered_at,
            }
            for i in items
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/failures/{event_id}", response_model=FailureDetail)
def get_failure(event_id: str, db: Session = Depends(get_db)):
    event = db.query(FailureEvent).filter(FailureEvent.id == event_id).first()
    if event is None:
        raise HTTPException(status_code=404, detail="not found")

    return FailureDetail(
        id=event.id,
        nomba_transaction_id=event.nomba_transaction_id,
        amount=event.amount,
        currency=event.currency,
        classification=event.classification.value if event.classification else None,
        recovery_score=event.recovery_score,
        status=event.status.value if hasattr(event.status, "value") else event.status,
        recovery_message=event.recovery_message,
        created_at=event.created_at,
        recovered_at=event.recovered_at,
        recovery_checkout_url=event.recovery_checkout_url,
    )


@router.post("/failures/{event_id}/trigger-recovery", response_model=TriggerRecoveryResponse)
async def trigger_recovery_route(
    event_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    check_rate_limit(request, key_suffix=f"trigger:{event_id}")

    try:
        event = await trigger_recovery(
            event_id=event_id,
            db=db,
            callback_base_url=str(request.base_url).rstrip("/"),
        )
    except RecoveryError:
        raise HTTPException(status_code=404, detail="not found")

    return TriggerRecoveryResponse(
        id=event.id,
        status=event.status.value if hasattr(event.status, "value") else event.status,
        recovery_checkout_url=event.recovery_checkout_url,
        triggered_at=event.updated_at,
    )
