import json
import tempfile
import unittest
from pathlib import Path

from config import StorageError, ValidationError
from storage import JsonStorage
from tests import LibraryTestCase


class StorageTests(LibraryTestCase):
    def setUp(self):
        super().setUp()
        # these tests need a pristine, seeded data folder instead of the shared fixtures
        self.app.activity.close()
        self._tmp.cleanup()
        self._tmp = tempfile.TemporaryDirectory()
        self.app = self.make_app(seed=True)

    def books_path(self):
        return Path(self._tmp.name) / "data" / "books.json"

    def restart(self):
        self.app.activity.close()
        self.app = self.make_app()

    def test_seeded_defaults(self):
        self.assertEqual(len(self.app.storage.default_for("books")), 40)
        self.assertEqual({e["status"] for e in self.app.startup_report}, {"Created default"})

    def test_atomic_write_leaves_no_temp_files(self):
        self.app.storage.write("fines", [])
        self.assertEqual(list(self.books_path().parent.glob("*.tmp")), [])
        self.assertEqual(self.app.storage.read("fines"), [])

    def test_unserializable_data_does_not_damage_file(self):
        before = self.books_path().read_text()
        with self.assertRaises(StorageError):
            self.app.storage.write("books", [{"bad": object()}])
        self.assertEqual(self.books_path().read_text(), before)
        self.assertEqual(list(self.books_path().parent.glob("*.tmp")), [])

    def test_corrupted_file_restored_from_latest_backup(self):
        self.app.backup.create_backup("test")
        self.books_path().write_text("{ this is not json", encoding="utf-8")
        self.restart()
        entry = next(e for e in self.app.startup_report if e["file"] == "books.json")
        self.assertEqual(entry["status"], "Restored from backup")
        self.assertEqual(len(self.app.books.list_all()), 40)

    def test_empty_or_wrong_structure_is_detected(self):
        self.app.backup.create_backup("test")
        for content in ("", "[]garbage", json.dumps({"not": "a list"}), json.dumps([{"book_id": "B1"}])):
            self.books_path().write_text(content, encoding="utf-8")
            self.restart()
            entry = next(e for e in self.app.startup_report if e["file"] == "books.json")
            self.assertEqual(entry["status"], "Restored from backup", content)

    def test_corrupted_without_backup_recreates_default(self):
        self.books_path().write_text("oops", encoding="utf-8")
        self.restart()
        entry = next(e for e in self.app.startup_report if e["file"] == "books.json")
        self.assertEqual(entry["status"], "Created default")

    def test_damaged_backup_is_skipped(self):
        good = self.app.backup.create_backup("good")
        self.clock.advance(minutes=5)
        bad = self.app.backup.create_backup("bad")
        (Path(self._tmp.name) / "backups" / bad / "books.json").write_text("broken", encoding="utf-8")
        self.books_path().write_text("broken", encoding="utf-8")
        self.restart()
        self.assertEqual(len(self.app.books.list_all()), 40)
        status = {b["name"]: b["valid"] for b in self.app.backup.list_backups()}
        self.assertTrue(status[good])
        self.assertFalse(status[bad])

    def test_backup_list_and_full_restore(self):
        name = self.app.backup.create_backup("manual")
        self.assertIn(name, [b["name"] for b in self.app.backup.list_backups()])
        self.app.books.add_book(self.admin, "9780306406157", "Temporary", "A", "C", 1)
        self.assertEqual(len(self.app.books.list_all()), 41)
        self.app.backup.restore_backup(name)
        self.app.reload_all()
        self.assertEqual(len(self.app.books.list_all()), 40)
        reasons = [e["detail"] for e in self.app.activity.read(action="BACKUP")]
        self.assertTrue(any("pre-restore" in r for r in reasons))  # safety backup taken first

    def test_restore_unknown_backup_rejected(self):
        with self.assertRaises(ValidationError):
            self.app.backup.restore_backup("1999-01-01_00-00-00")

    def test_old_backups_are_pruned(self):
        self.app.backup.max_backups = 3
        for _ in range(6):
            self.app.backup.create_backup("loop")
        self.assertEqual(len(self.app.backup.list_backups()), 3)

    def test_validate_helper(self):
        self.assertTrue(JsonStorage.validate("settings", {}))
        self.assertFalse(JsonStorage.validate("books", {}))
        self.assertFalse(JsonStorage.validate("books", [{"book_id": "B1", "isbn": "x", "title": "t",
                                                         "total_copies": 1, "available_copies": 5}]))

    def test_activity_log_filters(self):
        self.app.activity.log("riya", "Member", "LOGIN_FAILED", "FAIL")
        self.app.activity.log("lib1", "Librarian", "BORROW", "OK")
        self.assertEqual(len(list(self.app.activity.read(user="riya"))), 1)
        self.assertEqual(len(list(self.app.activity.read(action="borrow"))), 1)
        self.assertEqual(len(list(self.app.activity.read(day="2026-10-31"))) >= 2, True)


if __name__ == "__main__":
    unittest.main()
