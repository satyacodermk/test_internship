import unittest

from config import ValidationError
from models import ReservationStatus as S
from tests import LibraryTestCase


class ReservationTests(LibraryTestCase):
    def setUp(self):
        super().setUp()
        self.txn = self.borrow(self.riya, self.rare)  # the only copy is out

    def reserve(self, member):
        return self.app.reservations.reserve(member, member.user_id, self.rare.book_id)

    def test_cannot_reserve_available_book(self):
        with self.assertRaises(ValidationError):
            self.app.reservations.reserve(self.amit, self.amit.user_id, self.book.book_id)

    def test_duplicate_reservation_rejected(self):
        self.reserve(self.amit)
        with self.assertRaises(ValidationError):
            self.reserve(self.amit)

    def test_cannot_reserve_book_you_hold(self):
        with self.assertRaises(ValidationError):
            self.reserve(self.riya)

    def test_queue_positions(self):
        _, first = self.reserve(self.amit)
        _, second = self.reserve(self.neha)
        self.assertEqual((first, second), (1, 2))

    def test_return_allocates_to_first_in_line_only(self):
        a, _ = self.reserve(self.amit)
        n, _ = self.reserve(self.neha)
        result = self.app.borrowing.return_book(self.lib, self.txn.txn_id)
        self.assertEqual(result["next_reservation"].reservation_id, a.reservation_id)
        self.assertEqual((a.status, n.status), (S.READY, S.WAITING))
        self.assertEqual(self.rare.available_copies, 0)  # held, not on the shelf
        self.assertEqual(a.pickup_by, self.clock.today() + __import__("datetime").timedelta(days=2))

    def test_return_without_queue_restores_copy(self):
        self.app.borrowing.return_book(self.lib, self.txn.txn_id)
        self.assertEqual(self.rare.available_copies, 1)

    def test_held_copy_cannot_be_taken_by_someone_else(self):
        self.reserve(self.amit)
        self.app.borrowing.return_book(self.lib, self.txn.txn_id)
        from config import BookUnavailable
        with self.assertRaises(BookUnavailable):
            self.borrow(self.neha, self.rare)

    def test_collecting_creates_loan_without_changing_stock(self):
        a, _ = self.reserve(self.amit)
        self.app.borrowing.return_book(self.lib, self.txn.txn_id)
        result = self.app.reservations.mark_collected(self.lib, a.reservation_id)
        self.assertEqual(a.status, S.COLLECTED)
        self.assertEqual(result["txn"].member_id, self.amit.user_id)
        self.assertEqual(self.rare.available_copies, 0)

    def test_expiry_moves_hold_to_next_member(self):
        a, _ = self.reserve(self.amit)
        n, _ = self.reserve(self.neha)
        self.app.borrowing.return_book(self.lib, self.txn.txn_id)
        self.clock.advance(days=2)
        self.assertEqual(self.app.reservations.expire_overdue(), [])  # still inside the pickup window
        self.clock.advance(days=1)
        outcomes = self.app.reservations.expire_overdue()
        self.assertEqual([(e.reservation_id, p.reservation_id) for e, p in outcomes],
                         [(a.reservation_id, n.reservation_id)])
        self.assertEqual((a.status, n.status), (S.EXPIRED, S.READY))

    def test_expiry_with_empty_queue_returns_copy_to_shelf(self):
        a, _ = self.reserve(self.amit)
        self.app.borrowing.return_book(self.lib, self.txn.txn_id)
        self.clock.advance(days=5)
        self.app.reservations.expire_overdue()
        self.assertEqual(self.rare.available_copies, 1)

    def test_two_holds_expiring_same_day(self):
        other = self.add_book("9780306406157", "Second Rare", copies=1)
        txn2 = self.borrow(self.amit, other)
        a, _ = self.app.reservations.reserve(self.neha, self.neha.user_id, self.rare.book_id)
        b, _ = self.app.reservations.reserve(self.neha, self.neha.user_id, other.book_id)
        self.app.borrowing.return_book(self.lib, self.txn.txn_id)
        self.app.borrowing.return_book(self.lib, txn2.txn_id)
        self.clock.advance(days=3)
        self.assertEqual(len(self.app.reservations.expire_overdue()), 2)
        self.assertEqual((self.rare.available_copies, other.available_copies), (1, 1))

    def test_cancel_ready_reservation_promotes_next(self):
        a, _ = self.reserve(self.amit)
        n, _ = self.reserve(self.neha)
        self.app.borrowing.return_book(self.lib, self.txn.txn_id)
        self.app.reservations.cancel(self.amit, a.reservation_id)
        self.assertEqual((a.status, n.status), (S.CANCELLED, S.READY))

    def test_member_cannot_cancel_others_reservation(self):
        from config import PermissionDenied
        a, _ = self.reserve(self.amit)
        with self.assertRaises(PermissionDenied):
            self.app.reservations.cancel(self.neha, a.reservation_id)

    def test_inactive_member_in_queue_is_skipped(self):
        self.reserve(self.amit)
        n, _ = self.reserve(self.neha)
        self.app.users.deactivate(self.lib, self.amit.user_id)  # cancels amit's reservation
        self.app.borrowing.return_book(self.lib, self.txn.txn_id)
        self.assertEqual(n.status, S.READY)

    def test_adding_copies_fills_the_queue(self):
        n, _ = self.reserve(self.neha)
        self.app.books.update_book(self.admin, self.rare.book_id, total_copies=2)
        self.assertEqual(n.status, S.READY)


if __name__ == "__main__":
    unittest.main()
