"""Borrowing, returning and extending loans."""
from datetime import date, timedelta

from config import (BookUnavailable, NotFoundError, ValidationError, audited, check_member_scope)
from models import Transaction
from storage import PersistentCollection, UnitOfWork


class BorrowingService(PersistentCollection):
    FILE = "transactions"

    def __init__(self, storage, activity, settings, users, books, fines, reservations, today=date.today):
        super().__init__(storage)
        self.activity, self.settings = activity, settings
        self.users, self.books, self.fines, self.reservations = users, books, fines, reservations
        self.today = today
        self._txns = {}
        self.reload()

    def _serialize(self):
        return [t.to_dict() for t in self._txns.values()]

    def _load(self, raw):
        self._txns = {}
        for row in raw:
            txn = Transaction.from_dict(row)
            self._txns[txn.txn_id] = txn

    # -- queries
    def find(self, txn_id):
        return self._txns.get(str(txn_id).strip().upper())

    def get(self, txn_id):
        txn = self.find(txn_id)
        if txn is None:
            raise NotFoundError(f"Transaction '{txn_id}' was not found.")
        return txn

    def all_transactions(self):
        return list(self._txns.values())

    def active_loans(self):
        return [t for t in self._txns.values() if t.is_active]

    def loans_for_member(self, member_id):
        return [t for t in self._txns.values() if t.is_active and t.member_id == member_id]

    def history_for_member(self, member_id):
        return sorted((t for t in self._txns.values() if t.member_id == member_id),
                      key=lambda t: (t.borrowed_on, t.txn_id), reverse=True)

    def overdue_loans(self):
        today = self.today()
        return sorted((t for t in self._txns.values() if t.is_overdue(today)), key=lambda t: t.due_date)

    def active_count_for_book(self, book_id):
        return sum(1 for t in self._txns.values() if t.is_active and t.book_id == book_id)

    def has_active_loans(self, member_id):
        return bool(self.loans_for_member(member_id))

    def find_active(self, member_id, book_ref):
        book = self.books.resolve(book_ref)
        member_id = member_id.strip().upper()
        return next((t for t in self.loans_for_member(member_id) if t.book_id == book.book_id), None)

    def accrued_fine(self, txn):
        """Fine building up on a loan that is still out and overdue."""
        return self.fines.amount_for(txn.days_overdue(self.today())) if txn.is_active else 0

    def member_accrued(self, member_id):
        return sum(self.accrued_fine(t) for t in self.loans_for_member(member_id))

    def _next_txn_id(self, today):
        prefix = f"TXN-{today:%Y%m%d}-"
        numbers = [int(t[len(prefix):]) for t in self._txns if t.startswith(prefix) and t[len(prefix):].isdigit()]
        return f"{prefix}{max(numbers, default=0) + 1:04d}"

    # -- borrowing
    def evaluate_borrow(self, member_id, book_ref):
        """Run every borrowing rule without changing data.

        Returns (checks, member, book, hold); each check is (label, passed, failure_message, error_class).
        """
        member = self.users.get_member(member_id.strip().upper())
        book = self.books.resolve(book_ref)
        hold = self.reservations.ready_for(member.user_id, book.book_id)
        active = self.loans_for_member(member.user_id)
        limit = self.settings.get("borrow_limit")
        owed = self.fines.balance_for(member.user_id) + self.member_accrued(member.user_id)
        has_copy = hold is not None or book.available_copies > 0
        checks = [
            ("Member is active", member.is_active, "Member account is inactive.", ValidationError),
            (f"Under borrow limit ({len(active)}/{limit})", len(active) < limit, "Borrow limit reached.",
             ValidationError),
            ("Fines within limit", self.fines.within_limit(owed), "Outstanding fines exceed limit.",
             ValidationError),
            ("No duplicate loan of this title", all(t.book_id != book.book_id for t in active),
             "This member already has this book on loan.", ValidationError),
            ("Copy available", has_copy, "No copies available right now. You can reserve this book.",
             BookUnavailable),
        ]
        return checks, member, book, hold

    @audited("BORROW", lambda r: f"{r['txn'].txn_id} {r['member'].user_id} {r['book'].book_id}")
    def issue_book(self, actor, member_id, book_ref):
        member_id = member_id.strip().upper()
        check_member_scope(actor, member_id, "borrow_self", "issue_book", self.activity)
        self.reservations.expire_overdue()
        checks, member, book, hold = self.evaluate_borrow(member_id, book_ref)
        for _label, passed, message, error_class in checks:
            if not passed:
                raise error_class(message)
        today = self.today()
        with UnitOfWork(self, self.books, self.reservations):
            if hold:
                hold.mark_collected(today)  # the copy was already set aside for this member
            else:
                book.checkout()
            txn = Transaction(self._next_txn_id(today), member.user_id, book.book_id, today,
                              today + timedelta(days=self.settings.get("borrow_days")))
            self._txns[txn.txn_id] = txn
        return {"txn": txn, "member": member, "book": book, "copies_left": book.available_copies,
                "from_hold": hold is not None}

    # -- returning
    @audited("RETURN", lambda r: f"{r['txn'].txn_id} late {r['days_late']}d fine {r['fine'].amount if r['fine'] else 0}")
    def return_book(self, actor, txn_id):
        txn = self.get(txn_id)
        check_member_scope(actor, txn.member_id, "return_self", "return_any", self.activity)
        if not txn.is_active:
            raise ValidationError(f"{txn.txn_id} was already returned on {txn.returned_on}.")
        today = self.today()
        book = self.books.get(txn.book_id)
        days_late = self.fines.days_late(txn.due_date, today)
        with UnitOfWork(self, self.books, self.fines, self.reservations):
            txn.mark_returned(today, days_late)
            fine = self.fines.create_fine(txn.member_id, txn.txn_id, days_late)
            next_reservation = self.reservations.allocate_returned_copy(book)
        return {"txn": txn, "book": book, "days_late": days_late, "fine": fine,
                "next_reservation": next_reservation}

    # -- extensions
    @audited("EXTEND", lambda t: f"{t.txn_id} new due {t.due_date}")
    def extend(self, actor, txn_id):
        txn = self.get(txn_id)
        check_member_scope(actor, txn.member_id, "extend_self", "extend_any", self.activity)
        today = self.today()
        if not txn.is_active:
            raise ValidationError(f"{txn.txn_id} cannot be extended: book already returned")
        if txn.is_overdue(today):
            raise ValidationError(f"{txn.txn_id} cannot be extended: book is overdue")
        if self.reservations.waiting_count(txn.book_id):
            raise ValidationError(f"{txn.txn_id} cannot be extended: other members are waiting for this book")
        if self.fines.balance_for(txn.member_id) > 0:
            raise ValidationError(f"{txn.txn_id} cannot be extended: clear your fines first")
        if txn.extensions >= self.settings.get("max_extensions"):
            raise ValidationError(f"{txn.txn_id} cannot be extended: extension limit reached")
        with UnitOfWork(self):
            txn.extend(self.settings.get("extension_days"))
        return txn
