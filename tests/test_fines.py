import unittest

from config import PermissionDenied, ValidationError
from services.fine_service import FineService
from tests import LibraryTestCase


class FineTests(LibraryTestCase):
    def late_return(self, days_late=4, member=None, book=None):
        txn = self.borrow(member or self.riya, book)
        self.clock.advance(days=14 + days_late)
        return self.app.borrowing.return_book(self.lib, txn.txn_id)

    def test_days_late_never_negative(self):
        from datetime import date
        self.assertEqual(FineService.days_late(date(2026, 11, 14), date(2026, 11, 10)), 0)
        self.assertEqual(FineService.days_late(date(2026, 11, 14), date(2026, 11, 14)), 0)
        self.assertEqual(FineService.days_late(date(2026, 11, 14), date(2026, 11, 18)), 4)

    def test_fine_uses_configurable_rate(self):
        self.app.settings.update(self.admin, "fine_rate", 10)
        self.assertEqual(self.late_return(3)["fine"].amount, 30)

    def test_partial_and_full_payment(self):
        self.late_return(4)  # Rs 20
        receipt = self.app.fines.record_payment(self.lib, self.riya.user_id, 15)
        self.assertEqual((receipt["paid"], receipt["balance"]), (15, 5))
        receipt = self.app.fines.record_payment(self.lib, self.riya.user_id, 5)
        self.assertEqual(receipt["balance"], 0)

    def test_overpayment_zero_and_negative_rejected(self):
        self.late_return(4)
        for bad in (21, 0, -5):
            with self.assertRaises(ValidationError):
                self.app.fines.record_payment(self.lib, self.riya.user_id, bad)
        self.assertEqual(self.app.fines.balance_for(self.riya.user_id), 20)

    def test_payment_without_fines_rejected(self):
        with self.assertRaises(ValidationError):
            self.app.fines.record_payment(self.lib, self.amit.user_id, 5)

    def test_member_cannot_record_payment(self):
        self.late_return(4)
        with self.assertRaises(PermissionDenied):
            self.app.fines.record_payment(self.riya, self.riya.user_id, 5)

    def test_fine_above_limit_blocks_borrowing_until_paid(self):
        self.late_return(21)  # Rs 105 > limit Rs 100
        with self.assertRaises(ValidationError) as ctx:
            self.borrow(self.riya, self.rare)
        self.assertIn("fines exceed limit", str(ctx.exception))
        self.app.fines.record_payment(self.lib, self.riya.user_id, 5)  # balance 100 == limit -> allowed
        self.assertEqual(self.borrow(self.riya, self.rare).book_id, self.rare.book_id)

    def test_accruing_fines_on_overdue_loan_count_towards_limit(self):
        self.borrow(self.riya)
        self.clock.advance(days=14 + 21)  # 21 days overdue, still not returned
        with self.assertRaises(ValidationError):
            self.borrow(self.riya, self.rare)

    def test_fines_block_extension_and_deactivation(self):
        self.late_return(2)
        txn = self.borrow(self.riya, self.rare)
        with self.assertRaises(ValidationError):
            self.app.borrowing.extend(self.riya, txn.txn_id)
        self.app.borrowing.return_book(self.lib, txn.txn_id)
        with self.assertRaises(ValidationError) as ctx:
            self.app.users.deactivate(self.lib, self.riya.user_id)
        self.assertIn("unpaid fines", str(ctx.exception))

    def test_payments_are_logged(self):
        self.late_return(2)
        self.app.fines.record_payment(self.lib, self.riya.user_id, 5)
        self.assertTrue(list(self.app.activity.read(action="FINE_PAYMENT")))

    def test_overdue_report_and_reports_rbac(self):
        self.borrow(self.riya)
        self.clock.advance(days=14 + 21)
        report = self.app.reports.generate(self.lib, "overdue")
        self.assertIn("Fine: Rs 105", report["rows"][0][-1])
        self.assertEqual(report["footer"], ["Total outstanding fines: Rs 105"])
        with self.assertRaises(PermissionDenied):
            self.app.reports.generate(self.lib, "monthly")
        self.assertEqual(sum(v for _, v in self.app.reports.generate(self.admin, "monthly")["chart"]), 1)


if __name__ == "__main__":
    unittest.main()
