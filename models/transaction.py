"""Borrowing transaction model."""
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

from config import ValidationError


class TransactionStatus(Enum):
    ACTIVE = "ACTIVE"
    RETURNED = "RETURNED"


@dataclass
class Transaction:
    txn_id: str
    member_id: str
    book_id: str
    borrowed_on: date
    due_date: date
    returned_on: date | None = None
    status: TransactionStatus = TransactionStatus.ACTIVE
    extensions: int = 0
    days_late: int = 0

    @property
    def is_active(self):
        return self.status is TransactionStatus.ACTIVE

    def days_overdue(self, today):
        """Days past the due date (0 on the due date itself)."""
        if not self.is_active:
            return self.days_late
        return max(0, (today - self.due_date).days)

    def is_overdue(self, today):
        return self.is_active and today > self.due_date

    def days_left(self, today):
        return (self.due_date - today).days

    def mark_returned(self, on_date, days_late):
        if not self.is_active:
            raise ValidationError(f"{self.txn_id} was already returned on {self.returned_on}.")
        self.returned_on, self.days_late, self.status = on_date, days_late, TransactionStatus.RETURNED

    def extend(self, days):
        self.due_date += timedelta(days=days)
        self.extensions += 1

    def to_dict(self):
        return {
            "txn_id": self.txn_id, "member_id": self.member_id, "book_id": self.book_id,
            "borrowed_on": self.borrowed_on.isoformat(), "due_date": self.due_date.isoformat(),
            "returned_on": self.returned_on.isoformat() if self.returned_on else None,
            "status": self.status.value, "extensions": self.extensions, "days_late": self.days_late,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(d["txn_id"], d["member_id"], d["book_id"], date.fromisoformat(d["borrowed_on"]),
                   date.fromisoformat(d["due_date"]),
                   date.fromisoformat(d["returned_on"]) if d.get("returned_on") else None,
                   TransactionStatus(d["status"]), d.get("extensions", 0), d.get("days_late", 0))
