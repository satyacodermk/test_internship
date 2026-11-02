"""Safe JSON storage: atomic writes, validation, start-up recovery and all-or-nothing units of work."""
import copy
import json
import os
import shutil
import uuid
from pathlib import Path

import config
from config import DATA_FILES, DEFAULT_SETTINGS, SAMPLE_BOOKS, SAMPLE_USERS, Role, StorageError

REQUIRED_KEYS = {
    "users": {"user_id", "username", "role", "password_hash", "salt"},
    "books": {"book_id", "isbn", "title", "total_copies", "available_copies"},
    "transactions": {"txn_id", "member_id", "book_id", "borrowed_on", "due_date", "status"},
    "reservations": {"reservation_id", "book_id", "member_id", "reserved_on", "status"},
    "fines": {"fine_id", "member_id", "txn_id", "days_late", "amount", "created_on"},
}


class JsonStorage:
    """Reads and writes the JSON data files (referred to by logical names such as 'books')."""

    def __init__(self, data_dir=None, seed=True):
        self.data_dir = Path(data_dir) if data_dir else config.DATA_DIR
        self.seed = seed
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def path(self, name):
        return self.data_dir / DATA_FILES[name]

    # -- raw access
    @staticmethod
    def load_file(path):
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise StorageError(f"{Path(path).name} cannot be read: {exc}") from exc
        if not text.strip():
            raise StorageError(f"{Path(path).name} is empty")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise StorageError(f"{Path(path).name} is corrupted: {exc}") from exc

    def read(self, name, default=None):
        """Return the parsed file; a missing file gives `default`; a broken file raises StorageError."""
        path = self.path(name)
        if not path.exists():
            return copy.deepcopy(default)
        return self.load_file(path)

    def write(self, name, data):
        """Atomic save: write a temp file, flush to disk, then replace the real file."""
        path = self.path(name)
        tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except (OSError, TypeError, ValueError) as exc:
            tmp.unlink(missing_ok=True)
            raise StorageError(f"Could not save {path.name}: {exc}") from exc

    # -- validation
    @staticmethod
    def validate(name, data):
        if name == "settings":
            return isinstance(data, dict)
        if not isinstance(data, list):
            return False
        required = REQUIRED_KEYS[name]
        if not all(isinstance(row, dict) and required.issubset(row) for row in data):
            return False
        if name == "books":
            ints = lambda v: isinstance(v, int) and not isinstance(v, bool)  # noqa: E731
            return all(ints(r["total_copies"]) and ints(r["available_copies"])
                       and 0 <= r["available_copies"] <= r["total_copies"] for r in data)
        return True

    # -- defaults
    def default_for(self, name):
        if name == "settings":
            return dict(DEFAULT_SETTINGS)
        if not self.seed:
            return []
        if name == "users":
            from models import Member, User
            users = []
            for user_id, username, full_name, role, password in SAMPLE_USERS:
                cls = Member if role is Role.MEMBER else User
                users.append(cls.create(user_id, username, full_name, password, role=role,
                                        email=f"{username}@library.local").to_dict())
            return users
        if name == "books":
            from models import Book
            return [Book(f"B{n:03d}", f"9780{100000000 + n * 7919:09d}", title, author, category, copies).to_dict()
                    for n, (title, author, category, copies) in enumerate(SAMPLE_BOOKS, 1)]
        return []

    # -- start-up check
    def initialize(self, backup=None):
        """Verify every data file; restore from backup or create defaults. Returns a status list."""
        report = []
        for name, filename in DATA_FILES.items():
            path = self.path(name)
            problem = None
            if not path.exists():
                problem = "missing"
            else:
                try:
                    problem = None if self.validate(name, self.load_file(path)) else "invalid"
                except StorageError:
                    problem = "corrupted"
            if problem is None:
                report.append({"file": filename, "status": "OK", "detail": filename})
                continue
            if problem != "missing":
                shutil.copy2(path, path.with_name(filename + ".corrupt"))
            restored_from = backup.restore_file(filename) if backup is not None else None
            if restored_from:
                report.append({"file": filename, "status": "Restored from backup",
                               "detail": f"{filename} {problem} - restored from backup {restored_from}"})
            else:
                self.write(name, self.default_for(name))
                report.append({"file": filename, "status": "Created default",
                               "detail": f"{filename} {problem} - created default"})
        return report


class PersistentCollection:
    """Base class for a service that owns one JSON file and can snapshot / restore its data."""

    FILE = ""

    def __init__(self, storage):
        self.storage = storage

    def _default(self):
        return []

    def _serialize(self):
        raise NotImplementedError

    def _load(self, raw):
        raise NotImplementedError

    def reload(self):
        self._load(self.storage.read(self.FILE, self._default()))

    def export(self):
        return copy.deepcopy(self._serialize())

    def restore(self, snapshot):
        self._load(copy.deepcopy(snapshot))

    def save(self):
        self.storage.write(self.FILE, self._serialize())


class UnitOfWork:
    """All-or-nothing change across several collections.

    Validate first, then mutate inside `with UnitOfWork(a, b):`. On exit every collection is saved;
    if anything fails, memory (and any file already written) is rolled back to the snapshot.
    """

    def __init__(self, *collections):
        unique = []
        for collection in collections:
            if all(collection is not other for other in unique):
                unique.append(collection)
        self._collections = unique
        self._snapshots = []

    def __enter__(self):
        self._snapshots = [(c, c.export()) for c in self._collections]
        return self

    def _rollback(self, rewrite_files):
        for collection, snapshot in self._snapshots:
            collection.restore(snapshot)
            if rewrite_files:
                try:
                    collection.save()
                except StorageError:
                    pass

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self._rollback(rewrite_files=False)
            return False
        try:
            for collection in self._collections:
                collection.save()
        except Exception as error:
            self._rollback(rewrite_files=True)
            if isinstance(error, StorageError):
                raise
            raise StorageError(f"Save failed and changes were rolled back: {error}") from error
        return False
