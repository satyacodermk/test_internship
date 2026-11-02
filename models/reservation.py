"""Reservation model with a small state machine."""
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

from config import ValidationError


class ReservationStatus(Enum):
    WAITING = "WAITING"
    READY = "READY"
    COLLECTED = "COLLECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


@dataclass
class Reservation:
    reservation_id: str
    book_id: str
    member_id: str
    reserved_on: date
    status: ReservationStatus = ReservationStatus.WAITING
    ready_on: date | None = None
    pickup_by: date | None = None
    closed_on: date | None = None

    @property
    def is_active(self):
        return self.status in (ReservationStatus.WAITING, ReservationStatus.READY)

    def _require(self, *allowed):
        if self.status not in allowed:
            raise ValidationError(f"Reservation {self.reservation_id} is {self.status.value}; action not allowed.")

    def mark_ready(self, today, pickup_days):
        self._require(ReservationStatus.WAITING)
        self.status, self.ready_on = ReservationStatus.READY, today
        self.pickup_by = today + timedelta(days=pickup_days)

    def mark_collected(self, today):
        self._require(ReservationStatus.READY)
        self.status, self.closed_on = ReservationStatus.COLLECTED, today

    def mark_expired(self, today):
        self._require(ReservationStatus.READY)
        self.status, self.closed_on = ReservationStatus.EXPIRED, today

    def cancel(self, today):
        self._require(ReservationStatus.WAITING, ReservationStatus.READY)
        self.status, self.closed_on = ReservationStatus.CANCELLED, today

    def to_dict(self):
        iso = lambda d: d.isoformat() if d else None  # noqa: E731
        return {"reservation_id": self.reservation_id, "book_id": self.book_id, "member_id": self.member_id,
                "reserved_on": iso(self.reserved_on), "status": self.status.value, "ready_on": iso(self.ready_on),
                "pickup_by": iso(self.pickup_by), "closed_on": iso(self.closed_on)}

    @classmethod
    def from_dict(cls, d):
        parse = lambda v: date.fromisoformat(v) if v else None  # noqa: E731
        return cls(d["reservation_id"], d["book_id"], d["member_id"], date.fromisoformat(d["reserved_on"]),
                   ReservationStatus(d["status"]), parse(d.get("ready_on")), parse(d.get("pickup_by")),
                   parse(d.get("closed_on")))
