"""Purchases imported from Klarna exports, and the questions asked about them.

Klarna has no API for customers, so the user downloads a CSV export now and then and
uploads it. Exports overlap, so an import merges: a purchase is identified by date, time,
merchant and amount (identical ones by how many times they occur), and a later export
updates its status ("Pending" becomes settled, or "Cancelled").
"""

from __future__ import annotations

import csv
import io
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from sqlalchemy import func, select

from app.persistence.database import Database
from app.persistence.models import PurchaseModel

KLARNA = "klarna"
KLARNA_COLUMNS = {"date", "time", "merchant", "amount", "currency"}
#: Statuses of purchases that never went through; they don't count as spending.
NOT_SPENT = {"cancelled", "incorrect details", "insufficient balance", "rejected", "refunded"}
MAX_IMPORT_ROWS = 50_000


class PurchaseImportError(ValueError):
    pass


@dataclass(frozen=True)
class ImportResult:
    rows: int
    added: int
    updated: int
    unchanged: int


def _amount(text: str, row: int) -> Decimal:
    try:
        return Decimal(text.replace(" ", "").replace(",", ".")).quantize(Decimal("0.01"))
    except InvalidOperation as error:
        raise PurchaseImportError(f"Row {row}: {text!r} is not an amount") from error


def counts_as_spent(status: str) -> bool:
    return status.strip().lower() not in NOT_SPENT


class PurchaseService:
    def __init__(self, database: Database):
        self.database = database

    def import_klarna_csv(self, text: str) -> ImportResult:
        reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
        missing = KLARNA_COLUMNS - set(reader.fieldnames or [])
        if missing:
            columns = ", ".join(sorted(missing))
            raise PurchaseImportError(f"This doesn't look like a Klarna export (no {columns})")
        parsed, seen = [], Counter()
        for number, row in enumerate(reader, start=2):
            if number > MAX_IMPORT_ROWS:
                raise PurchaseImportError(f"More than {MAX_IMPORT_ROWS} rows")
            try:
                occurred_on = date.fromisoformat(row["date"].strip())
            except ValueError as error:
                raise PurchaseImportError(f"Row {number}: {row['date']!r} is not a date") from error
            identity = (
                occurred_on,
                (row.get("time") or "").strip()[:5],
                (row.get("merchant") or "").strip()[:200] or "Unknown",
                _amount(row["amount"], number),
            )
            seen[identity] += 1
            parsed.append(
                {
                    "identity": identity,
                    "occurrence": seen[identity],
                    "currency": (row.get("currency") or "SEK").strip()[:3] or "SEK",
                    "original_amount": (
                        _amount(row["original_amount"], number)
                        if (row.get("original_amount") or "").strip()
                        else None
                    ),
                    "payment_type": (row.get("payment_type") or "").strip()[:64],
                    "status": (row.get("status") or "").strip()[:64],
                }
            )
        added = updated = 0
        with self.database.session() as session, session.begin():
            for item in parsed:
                occurred_on, occurred_time, merchant, amount = item["identity"]
                existing = session.scalar(
                    select(PurchaseModel).where(
                        PurchaseModel.source == KLARNA,
                        PurchaseModel.occurred_on == occurred_on,
                        PurchaseModel.occurred_time == occurred_time,
                        PurchaseModel.merchant == merchant,
                        PurchaseModel.amount == amount,
                        PurchaseModel.occurrence == item["occurrence"],
                    )
                )
                details = {
                    key: item[key]
                    for key in ("currency", "original_amount", "payment_type", "status")
                }
                if existing is None:
                    session.add(
                        PurchaseModel(
                            id=str(uuid4()),
                            source=KLARNA,
                            occurred_on=occurred_on,
                            occurred_time=occurred_time,
                            merchant=merchant,
                            amount=amount,
                            occurrence=item["occurrence"],
                            **details,
                        )
                    )
                    added += 1
                elif any(getattr(existing, key) != value for key, value in details.items()):
                    for key, value in details.items():
                        setattr(existing, key, value)
                    updated += 1
        return ImportResult(
            rows=len(parsed), added=added, updated=updated, unchanged=len(parsed) - added - updated
        )

    def overview(self) -> dict:
        with self.database.session() as session:
            count, first, last, imported = session.execute(
                select(
                    func.count(PurchaseModel.id),
                    func.min(PurchaseModel.occurred_on),
                    func.max(PurchaseModel.occurred_on),
                    func.max(PurchaseModel.imported_at),
                )
            ).one()
        return {"count": count, "first": first, "last": last, "last_imported_at": imported}

    def purchases(
        self,
        *,
        start: date | None = None,
        end: date | None = None,
        merchant: str | None = None,
        limit: int = 50,
    ) -> list[PurchaseModel]:
        """Newest first; merchant matches any part of the name, ignoring case."""
        query = select(PurchaseModel)
        if start:
            query = query.where(PurchaseModel.occurred_on >= start)
        if end:
            query = query.where(PurchaseModel.occurred_on <= end)
        if merchant:
            query = query.where(func.lower(PurchaseModel.merchant).contains(merchant.lower()))
        query = query.order_by(
            PurchaseModel.occurred_on.desc(), PurchaseModel.occurred_time.desc()
        ).limit(limit)
        with self.database.session() as session:
            return list(session.scalars(query))

    def spending(
        self,
        *,
        start: date | None = None,
        end: date | None = None,
        merchant: str | None = None,
        group_by: str | None = None,
    ) -> dict:
        """Totals of what was actually spent (cancelled and failed purchases left out)."""
        rows = self.purchases(start=start, end=end, merchant=merchant, limit=MAX_IMPORT_ROWS)
        spent = [row for row in rows if counts_as_spent(row.status)]
        groups: dict[str, list[PurchaseModel]] = defaultdict(list)
        for row in spent:
            key = (
                row.merchant
                if group_by == "merchant"
                else row.occurred_on.strftime("%Y-%m")
                if group_by == "month"
                else "all"
            )
            groups[key].append(row)
        totals = sorted(
            (
                {"group": key, "total": str(sum(r.amount for r in items)), "purchases": len(items)}
                for key, items in groups.items()
            ),
            key=lambda item: -Decimal(item["total"]),
        )
        if group_by == "month":
            totals.sort(key=lambda item: item["group"])
        return {
            "total": str(sum((row.amount for row in spent), Decimal("0"))),
            "currency": spent[0].currency if spent else "SEK",
            "purchases": len(spent),
            "not_counted": {
                "count": len(rows) - len(spent),
                "reason": "cancelled or failed (e.g. insufficient balance)",
            },
            "pending": sum(1 for row in spent if row.status.lower() == "pending"),
            "groups": totals[:40] if group_by else [],
        }
