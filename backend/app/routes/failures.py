import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import FailureEvent, FailureStatus
from app.schemas import (
    SummaryResponse,
    FailureListResponse,
    FailureDetail,
    TriggerRecoveryResponse,
    ClassificationBreakdownResponse,
    ClassificationBreakdownItem,
    TrendPoint,
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


@router.get("/summary/trend", response_model=list[TrendPoint])
def get_recovery_trend(days: int = Query(default=7, ge=1, le=90), db: Session = Depends(get_db)):
    """
    Cumulative recovery rate as of the end of each of the last `days`
    days — i.e. day N's point is "of everything captured up to and
    including day N, what share is now RECOVERED". Cumulative rather
    than a same-day-only cohort so the line is meaningful even on a
    fresh deployment with sparse daily volume, instead of jumping
    between 0% and 100% on days with only one or two events.

    NOTE: this endpoint did not previously exist — the frontend's
    getRecoveryTrend() always fell back to fixture data client-side
    when it 404'd, which is why the dashboard's trend chart kept
    showing a plausible-looking curve even while every other card
    correctly showed real zeros against a fresh deployment. This chart
    now reflects real data like the rest of the dashboard.
    """
    now = datetime.now(timezone.utc)
    points: list[TrendPoint] = []

    for i in range(days - 1, -1, -1):
        day = now - timedelta(days=i)
        end_of_day = datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=timezone.utc)

        total = (
            db.query(func.count(FailureEvent.id))
            .filter(FailureEvent.created_at <= end_of_day)
            .scalar()
            or 0
        )
        recovered = (
            db.query(func.count(FailureEvent.id))
            .filter(
                FailureEvent.created_at <= end_of_day,
                FailureEvent.status == FailureStatus.RECOVERED,
            )
            .scalar()
            or 0
        )
        rate = round((recovered / total) * 100, 1) if total else 0.0
        points.append(TrendPoint(date=day.strftime("%b %d"), recovery_rate=rate))

    return points


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
                "has_contact": bool(i.customer_email),
                "retry_count": i.retry_count,
                "next_retry_at": i.next_retry_at,
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
        has_contact=bool(event.customer_email),
        retry_count=event.retry_count,
        next_retry_at=event.next_retry_at,
    )


@router.get("/analytics/breakdown", response_model=ClassificationBreakdownResponse)
def get_classification_breakdown(db: Session = Depends(get_db)):
    """Recovery performance grouped by AI-classified failure reason —
    which failure types are most common, and which ones actually
    convert once a recovery link goes out. Powers the dashboard's
    'Recovery by Failure Reason' chart."""
    rows = (
        db.query(
            FailureEvent.classification,
            func.count(FailureEvent.id),
            func.coalesce(func.sum(FailureEvent.amount), 0),
        )
        .group_by(FailureEvent.classification)
        .all()
    )

    items = []
    for classification, count, total_amount in rows:
        recovered_count = (
            db.query(func.count(FailureEvent.id))
            .filter(
                FailureEvent.classification == classification,
                FailureEvent.status == FailureStatus.RECOVERED,
            )
            .scalar()
            or 0
        )
        recovered_amount = (
            db.query(func.coalesce(func.sum(FailureEvent.amount), 0))
            .filter(
                FailureEvent.classification == classification,
                FailureEvent.status == FailureStatus.RECOVERED,
            )
            .scalar()
            or 0
        )
        items.append(
            ClassificationBreakdownItem(
                classification=classification.value if classification else "UNCLASSIFIED",
                count=count,
                total_amount=int(total_amount),
                recovered_count=recovered_count,
                recovered_amount=int(recovered_amount),
                recovery_rate=round((recovered_count / count) * 100, 1) if count else 0.0,
            )
        )

    # Largest failure-count reason first — the merchant's biggest lever.
    items.sort(key=lambda i: i.count, reverse=True)
    return ClassificationBreakdownResponse(items=items)


@router.get("/export")
def export_failures_csv(db: Session = Depends(get_db)):
    """CSV export of every captured failure event, for merchants who
    want to pull this into their own spreadsheet/BI tool rather than
    reading it off the dashboard. No customer PII included, consistent
    with the rest of the public API — see FailureItem's has_contact
    note."""
    events = db.query(FailureEvent).order_by(FailureEvent.created_at.desc()).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "id",
            "nomba_transaction_id",
            "amount",
            "currency",
            "classification",
            "status",
            "recovery_score",
            "has_contact",
            "retry_count",
            "created_at",
            "recovered_at",
        ]
    )
    for e in events:
        writer.writerow(
            [
                e.id,
                e.nomba_transaction_id,
                e.amount,
                e.currency,
                e.classification.value if e.classification else "",
                e.status.value if hasattr(e.status, "value") else e.status,
                e.recovery_score if e.recovery_score is not None else "",
                bool(e.customer_email),
                e.retry_count,
                e.created_at.isoformat() if e.created_at else "",
                e.recovered_at.isoformat() if e.recovered_at else "",
            ]
        )
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=nombareclaim_export.csv"},
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
