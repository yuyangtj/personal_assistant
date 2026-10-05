"""Klarna purchases: importing exports and browsing what was imported."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.work.purchases import PurchaseImportError, PurchaseService

router = APIRouter()


class PurchaseImportRequest(BaseModel):
    source: str = Field(default="klarna", pattern="^klarna$")
    #: The export file's text; a few years of purchases is well under a megabyte.
    csv: str = Field(min_length=1, max_length=5_000_000)


def _purchases(request: Request) -> PurchaseService:
    return request.app.state.purchase_service


@router.post("/purchases/import")
def import_purchases(body: PurchaseImportRequest, request: Request) -> dict:
    try:
        result = _purchases(request).import_klarna_csv(body.csv)
    except PurchaseImportError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {**result.__dict__, "overview": _purchases(request).overview()}


@router.get("/purchases")
def list_purchases(
    request: Request,
    merchant: str | None = None,
    start: date | None = None,
    end: date | None = None,
    limit: int = 100,
) -> dict:
    service = _purchases(request)
    rows = service.purchases(start=start, end=end, merchant=merchant, limit=max(1, min(limit, 500)))
    return {
        "overview": service.overview(),
        "purchases": [
            {
                "id": row.id,
                "date": row.occurred_on,
                "time": row.occurred_time,
                "merchant": row.merchant,
                "amount": str(row.amount),
                "currency": row.currency,
                "payment_type": row.payment_type,
                "status": row.status,
            }
            for row in rows
        ],
    }
