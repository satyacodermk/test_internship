import unittest
from datetime import timedelta
from unittest import mock

from config import BookUnavailable, PermissionDenied, StorageError, ValidationError
from tests import LibraryTestCase


class BorrowingTests(LibraryTestCase):
    def test_borrow_creates_transaction_and_reduces_copies(self):
        result = self.app.borrowing.issue_book(self.lib, self.riya.user_id, self.book.book_id)
        txn = result["txn"]
        self.assertTrue(txn.txn_id.startswith("TXN-20261031-"))
        self.assertEqual(txn.due_date, self.clock.today() + timedelta(days=14))
        self.assertEqual(self.book.available_copies, 1)
        self.assertEqual(result["copies_left"], 1)

    def test_member_can_borrow_for_self_only(self):
        self.app.borrowing.issue_book(self.riya, self.riya.user_id, self.book.book_id)
        with self.assertRaises(PermissionDenied):
            self.app.borrowing.issue_book(self.riya, self.amit.user_id, self.rare.book_id)

    def test_borrow_limit(self):
        for n in range(3):
            self.borrow(self.riya, self.add_book(f"97800000000{n:02d}", f"Book {n}", copies=2))
        with self.assertRaises(ValidationError) as ctx:
            self.borrow(self.riya)
        self.assertIn("Borrow limit reached", str(ctx.exception))

    def test_unavailable_book_offers_reservation(self):
        self.borrow(self.riya, self.rare)
        with self.assertRaises(BookUnavailable) as ctx:
            self.borrow(self.amit, self.rare)
        self.assertIn("reserve", str(ctx.exception).lower())

    def test_duplicate_loan_of_same_title_blocked(self):
        self.borrow(self.riya)
        with self.assertRaises(ValidationError):
            self.borrow(self.riya)

    def test_inactive_member_cannot_borrow(self):
        self.app.users.deactivate(self.lib, self.neha.user_id)
        with self.assertRaises(ValidationError):
            self.borrow(self.neha)

    def test_double_return_rejected(self):
        txn = self.borrow(self.riya)
        self.app.borrowing.return_book(self.lib, txn.txn_id)
        with self.assertRaises(ValidationError) as ctx:
            self.app.borrowing.return_book(self.lib, txn.txn_id)
        self.assertIn("already returned", str(ctx.exception))
        self.assertEqual(self.book.available_copies, 2)

    def test_return_on_due_date_has_no_fine(self):
        txn = self.borrow(self.riya)
        self.clock.advance(days=14)
        result = self.app.borrowing.return_book(self.lib, txn.txn_id)
        self.assertEqual(result["days_late"], 0)
        self.assertIsNone(result["fine"])

    def test_late_return_creates_fine(self):
        txn = self.borrow(self.riya)
        self.clock.advance(days=18)
        result = self.app.borrowing.return_book(self.lib, txn.txn_id)
        self.assertEqual((result["days_late"], result["fine"].amount), (4, 20))
        self.assertEqual(self.app.fines.balance_for(self.riya.user_id), 20)

    def test_member_cannot_return_someone_elses_book(self):
        txn = self.borrow(self.riya)
        with self.assertRaises(PermissionDenied):
            self.app.borrowing.return_book(self.amit, txn.txn_id)

    def test_deactivated_member_loans_block_deactivation(self):
        self.borrow(self.riya)
        with self.assertRaises(ValidationError):
            self.app.users.deactivate(self.admin, self.riya.user_id)

    def test_extension_rules(self):
        txn = self.borrow(self.riya)
        self.app.borrowing.extend(self.riya, txn.txn_id)
        self.assertEqual(txn.due_date, self.clock.today() + timedelta(days=21))
        with self.assertRaises(ValidationError) as ctx:
            self.app.borrowing.extend(self.riya, txn.txn_id)
        self.assertIn("limit", str(ctx.exception))

    def test_overdue_loan_cannot_be_extended(self):
        txn = self.borrow(self.riya)
        self.clock.advance(days=15)
        with self.assertRaises(ValidationError) as ctx:
            self.app.borrowing.extend(self.riya, txn.txn_id)
        self.assertIn("overdue", str(ctx.exception))

    def test_extension_blocked_when_someone_waits(self):
        txn = self.borrow(self.riya, self.rare)
        self.app.reservations.reserve(self.amit, self.amit.user_id, self.rare.book_id)
        with self.assertRaises(ValidationError) as ctx:
            self.app.borrowing.extend(self.riya, txn.txn_id)
        self.assertIn("waiting", str(ctx.exception))

    def test_settings_change_does_not_alter_existing_due_dates(self):
        txn = self.borrow(self.riya)
        self.app.settings.update(self.admin, "borrow_days", 30)
        self.assertEqual(txn.due_date, self.clock.today() + timedelta(days=14))
        newer = self.borrow(self.amit)
        self.assertEqual(newer.due_date, self.clock.today() + timedelta(days=30))

    def test_failed_save_rolls_everything_back(self):
        real_write = self.app.storage.write

        def flaky(name, data):
            if name == "books":
                raise StorageError("disk full")
            return real_write(name, data)

        with mock.patch.object(self.app.storage, "write", side_effect=flaky):
            with self.assertRaises(StorageError):
                self.borrow(self.riya)
        self.assertEqual(self.app.books.get(self.book.book_id).available_copies, 2)
        self.assertEqual(self.app.borrowing.all_transactions(), [])
        self.assertEqual(self.app.storage.read("transactions"), [])


if __name__ == "__main__":
    unittest.main()
