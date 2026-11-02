"""Reservations with FIFO waiting queues, auto-allocation and expiry."""
from collections import defaultdict, deque
from datetime import date

from config import (NotFoundError, ValidationError, audited, check_member_scope, check_permission,
                    next_number)
from models import Reservation, ReservationStatus
from storage import PersistentCollection, UnitOfWork

WAITING, READY = ReservationStatus.WAITING, ReservationStatus.READY


class ReservationService(PersistentCollection):
    FILE = "reservations"

    def __init__(self, storage, activity, settings, books, today=date.today):
        super().__init__(storage)
        self.activity, self.settings, self.books, self.today = activity, settings, books, today
        self.users = self.borrowing = None
        self._items = []
        self._by_book = defaultdict(list)
        self.reload()

    def attach(self, users, borrowing):
        self.users, self.borrowing = users, borrowing

    def _serialize(self):
        return [r.to_dict() for r in self._items]

    def _load(self, raw):
        self._items = [Reservation.from_dict(row) for row in raw]
        self._by_book = defaultdict(list)
        for item in self._items:
            self._by_book[item.book_id].append(item)

    # -- queries
    def get(self, reservation_id):
        wanted = reservation_id.strip().upper()
        for item in self._items:
            if item.reservation_id == wanted:
                return item
        raise NotFoundError(f"Reservation '{reservation_id}' was not found.")

    def all_for_book(self, book_id):
        return list(self._by_book.get(book_id, []))

    def active_for_book(self, book_id):
        return [r for r in self._by_book.get(book_id, []) if r.is_active]

    def waiting_queue(self, book_id):
        """FIFO queue (collections.deque) of members still waiting for a copy."""
        return deque(r for r in self._by_book.get(book_id, []) if r.status is WAITING)

    def waiting_count(self, book_id):
        return len(self.waiting_queue(book_id))

    def position_of(self, reservation):
        """1-based place in the waiting line, or None if not waiting."""
        for index, item in enumerate(self.waiting_queue(reservation.book_id), 1):
            if item is reservation:
                return index
        return None

    def for_member(self, member_id, active_only=False):
        return [r for r in self._items if r.member_id == member_id and (r.is_active or not active_only)]

    def ready_reservations(self):
        return [r for r in self._items if r.status is READY]

    def ready_for(self, member_id, book_id):
        return next((r for r in self._by_book.get(book_id, []) if r.member_id == member_id and r.status is READY), None)

    # -- queue mechanics (no saving - callers wrap these in a UnitOfWork)
    def allocate_returned_copy(self, book):
        """Give a freed copy to the first valid member in line, otherwise put it back on the shelf."""
        today = self.today()
        queue = self.waiting_queue(book.book_id)
        while queue:
            reservation = queue.popleft()
            member = self.users.find(reservation.member_id)
            if member is None or not member.is_active:
                reservation.cancel(today)
                continue
            reservation.mark_ready(today, self.settings.get("pickup_days"))
            return reservation
        book.check_in()
        return None

    def fill_available(self, book):
        """Hand copies that are sitting on the shelf to waiting members (e.g. after stock was added)."""
        filled = []
        while book.available_copies > 0 and self.waiting_count(book.book_id) > 0:
            book.checkout()
            reservation = self.allocate_returned_copy(book)
            if reservation is None:
                break
            filled.append(reservation)
        return filled

    def cancel_all_for_member(self, member_id):
        today = self.today()
        for reservation in self.for_member(member_id, active_only=True):
            was_ready = reservation.status is READY
            reservation.cancel(today)
            if was_ready:
                self.allocate_returned_copy(self.books.get(reservation.book_id))

    # -- commands
    @audited("RESERVE", lambda r: f"{r[0].reservation_id} {r[0].book_id} position {r[1]}")
    def reserve(self, actor, member_id, book_ref):
        member_id = member_id.strip().upper()
        check_member_scope(actor, member_id, "reserve_self", "manage_reservations", self.activity)
        member = self.users.get_member(member_id)
        if not member.is_active:
            raise ValidationError("Member account is inactive.")
        book = self.books.resolve(book_ref)
        if book.available_copies > 0:
            raise ValidationError("Copies are available - borrow the book instead of reserving it.")
        if any(r.member_id == member_id for r in self.active_for_book(book.book_id)):
            raise ValidationError("You already have an active reservation for this book.")
        if any(t.book_id == book.book_id for t in self.borrowing.loans_for_member(member_id)):
            raise ValidationError("You already have this book on loan.")
        reservation = Reservation(f"R-{next_number(r.reservation_id for r in self._items):04d}",
                                  book.book_id, member_id, self.today())
        with UnitOfWork(self):
            self._items.append(reservation)
            self._by_book[book.book_id].append(reservation)
        return reservation, self.position_of(reservation)

    @audited("RESERVATION_CANCEL", lambda r: r.reservation_id)
    def cancel(self, actor, reservation_id):
        reservation = self.get(reservation_id)
        check_member_scope(actor, reservation.member_id, "cancel_own_reservation", "manage_reservations", self.activity)
        if not reservation.is_active:
            raise ValidationError(f"Reservation {reservation.reservation_id} is already {reservation.status.value}.")
        was_ready = reservation.status is READY
        with UnitOfWork(self, self.books):
            reservation.cancel(self.today())
            if was_ready:
                self.allocate_returned_copy(self.books.get(reservation.book_id))
        return reservation

    def expire_overdue(self):
        """Expire READY holds whose pickup date has passed and pass each copy to the next member in line."""
        today = self.today()
        due = sorted((r for r in self._items if r.status is READY and r.pickup_by and r.pickup_by < today),
                     key=lambda r: (r.pickup_by, r.reservation_id))
        if not due:
            return []
        outcomes = []
        with UnitOfWork(self, self.books):
            for reservation in due:
                reservation.mark_expired(today)
                promoted = self.allocate_returned_copy(self.books.get(reservation.book_id))
                outcomes.append((reservation, promoted))
        for reservation, promoted in outcomes:
            note = f"next: {promoted.member_id}" if promoted else "copy back on shelf"
            self.activity.log("system", "-", "RESERVATION_EXPIRED", "OK",
                              f"{reservation.reservation_id} {reservation.member_id} {reservation.book_id} ({note})")
        return outcomes

    def mark_collected(self, actor, reservation_id):
        """Staff hands the held copy to the member: this creates the loan transaction."""
        check_permission(actor, "manage_reservations", self.activity)
        self.expire_overdue()
        reservation = self.get(reservation_id)
        if reservation.status is not READY:
            raise ValidationError(f"Reservation {reservation.reservation_id} is {reservation.status.value}, not READY.")
        return self.borrowing.issue_book(actor, reservation.member_id, reservation.book_id)
