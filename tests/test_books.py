import unittest

from config import PermissionDenied, ValidationError
from tests import LibraryTestCase


class BookTests(LibraryTestCase):
    def test_duplicate_isbn_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            self.app.books.add_book(self.lib, "978-0-13-235088-4", "Another", "X", "Y", 1)
        self.assertIn("Duplicate ISBN", str(ctx.exception))

    def test_invalid_isbn_and_copies(self):
        with self.assertRaises(ValidationError):
            self.app.books.add_book(self.lib, "12345", "T", "A", "C", 1)
        with self.assertRaises(ValidationError):
            self.app.books.add_book(self.lib, "9780306406157", "T", "A", "C", -1)

    def test_member_cannot_add_book(self):
        with self.assertRaises(PermissionDenied):
            self.app.books.add_book(self.riya, "9780306406157", "T", "A", "C", 1)

    def test_delete_blocked_while_on_loan(self):
        self.borrow(self.riya)
        with self.assertRaises(ValidationError) as ctx:
            self.app.books.delete_book(self.admin, self.book.book_id)
        self.assertIn("currently borrowed", str(ctx.exception))

    def test_delete_blocked_with_active_reservation_then_allowed(self):
        self.borrow(self.riya, self.rare)
        self.app.reservations.reserve(self.amit, self.amit.user_id, self.rare.book_id)
        with self.assertRaises(ValidationError):
            self.app.books.delete_book(self.admin, self.rare.book_id)
        self.app.books.delete_book(self.admin, self.book.book_id)  # unused book can go
        self.assertIsNone(self.app.books.find(self.book.book_id))

    def test_update_total_copies_adjusts_availability(self):
        self.borrow(self.riya)
        self.app.books.update_book(self.admin, self.book.book_id, total_copies=5)
        self.assertEqual((self.book.total_copies, self.book.available_copies), (5, 4))
        with self.assertRaises(ValidationError):
            self.app.books.update_book(self.admin, self.book.book_id, total_copies=0)
        self.assertEqual(self.book.total_copies, 5)

    def test_search_is_partial_case_insensitive_and_filterable(self):
        self.assertEqual([b.title for b in self.app.books.search("title", "CLEAN")], ["Clean Code"])
        self.assertEqual(len(self.app.books.search("category", "program")), 2)
        self.assertEqual(len(self.app.books.search("category", "", category="programming")), 2)
        self.borrow(self.riya, self.rare)
        available = [b.title for b in self.app.books.search("title", "", available_only=True)]
        self.assertNotIn("Design Patterns", available)
        self.assertEqual(self.app.books.search("title", "zzz"), [])

    def test_resolve_by_isbn_and_unknown(self):
        self.assertEqual(self.app.books.resolve("978-0-13-235088-4").title, "Clean Code")
        from config import NotFoundError
        with self.assertRaises(NotFoundError):
            self.app.books.resolve("B999")

    def test_data_persists_between_runs(self):
        self.app.activity.close()
        again = self.make_app()
        self.assertEqual(len(again.books.list_all()), 2)
        again.activity.close()


if __name__ == "__main__":
    unittest.main()
