"""Shared test helpers: temp folders (real data is never touched), a fake clock and ready-made users/books."""
import tempfile
import unittest
from datetime import date, datetime, timedelta

import config
from config import Role
from models import User
from services import ServiceContainer


class FakeClock:
    def __init__(self, start=date(2026, 10, 31)):
        self.current = start

    def today(self):
        return self.current

    def now(self):
        return datetime.combine(self.current, datetime.min.time()).replace(hour=9) + getattr(self, "offset", timedelta())

    def advance(self, days=0, minutes=0):
        self.current += timedelta(days=days)
        self.offset = getattr(self, "offset", timedelta()) + timedelta(minutes=minutes)


class LibraryTestCase(unittest.TestCase):
    seed = False

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._iterations = config.HASH_ITERATIONS
        config.HASH_ITERATIONS = 1000  # fast hashing for tests
        self.clock = FakeClock()
        self.app = self.make_app()
        self.admin = User.create("U900", "root", "Root Admin", "secret1", role=Role.ADMIN)
        self.lib = self.app.users.add_user(self.admin, "librarian1", "Lib Rarian", Role.LIBRARIAN, "lib1234")
        self.riya = self.app.users.add_user(self.lib, "riya", "Riya Patil", Role.MEMBER, "riya123")
        self.amit = self.app.users.add_user(self.lib, "amit", "Amit Joshi", Role.MEMBER, "amit123")
        self.neha = self.app.users.add_user(self.lib, "neha", "Neha Verma", Role.MEMBER, "neha123")
        self.book = self.add_book("9780132350884", "Clean Code", copies=2)
        self.rare = self.add_book("9780201633610", "Design Patterns", copies=1)

    def tearDown(self):
        self.app.shutdown()
        config.HASH_ITERATIONS = self._iterations
        self._tmp.cleanup()

    def make_app(self, seed=None):
        return ServiceContainer(base_dir=self._tmp.name, seed=self.seed if seed is None else seed,
                                today=self.clock.today, now=self.clock.now)

    def add_book(self, isbn, title, copies=1, author="Author", category="Programming"):
        return self.app.books.add_book(self.admin, isbn, title, author, category, copies)

    def borrow(self, member, book=None):
        return self.app.borrowing.issue_book(self.lib, member.user_id, (book or self.book).book_id)["txn"]
