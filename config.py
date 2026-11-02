"""Central configuration: paths, roles, permissions (RBAC), settings, errors and activity logging."""
import functools
import logging
from datetime import datetime
from enum import Enum
from pathlib import Path

APP_NAME = "SMART LIBRARY SYSTEM"
APP_VERSION = "1.0"
CURRENCY = "Rs"

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
BACKUP_DIR = BASE_DIR / "backups"
LOG_FILE_NAME = "activity.log"

DATA_FILES = {
    "settings": "settings.json",
    "users": "users.json",
    "books": "books.json",
    "transactions": "transactions.json",
    "reservations": "reservations.json",
    "fines": "fines.json",
}

MAX_BACKUPS = 25
HASH_ITERATIONS = 100_000
MIN_PASSWORD_LENGTH = 6
PAGE_SIZE = 10


# --------------------------------------------------------------------------- errors
class LibraryError(Exception):
    """Base class for every expected, user-facing error."""


class ValidationError(LibraryError):
    """A business rule or input check failed."""


class NotFoundError(LibraryError):
    """A requested record does not exist."""


class BookUnavailable(ValidationError):
    """No copy can be borrowed right now (the book can be reserved instead)."""


class PermissionDenied(LibraryError):
    """The user's role does not allow this action."""


class StorageError(LibraryError):
    """A JSON file could not be read, written or validated."""


class AuthError(LibraryError):
    """Login failed."""

    def __init__(self, message, attempts_left=None):
        super().__init__(message)
        self.attempts_left = attempts_left


# --------------------------------------------------------------------------- roles & RBAC
class Role(Enum):
    ADMIN = "Admin"
    LIBRARIAN = "Librarian"
    MEMBER = "Member"


PERMISSIONS = {
    Role.ADMIN: {
        "manage_users", "register_member", "manage_books", "manage_settings", "view_logs",
        "manage_backups", "search_books", "view_ops_reports", "view_all_reports",
    },
    Role.LIBRARIAN: {
        "manage_books", "register_member", "manage_members", "search_books", "issue_book",
        "return_any", "extend_any", "manage_reservations", "manage_fines", "view_ops_reports",
    },
    Role.MEMBER: {
        "search_books", "borrow_self", "reserve_self", "return_self", "extend_self",
        "cancel_own_reservation", "view_own",
    },
}


def has_permission(actor, permission):
    """True when the actor's role includes the permission."""
    return actor is not None and permission in PERMISSIONS.get(actor.role, set())


def _deny(actor, permission, activity):
    if activity is not None:
        activity.log_actor(actor, "ACCESS_DENIED", "FAIL", permission)
    role = actor.role.value if actor is not None else "Guest"
    raise PermissionDenied(f"Access denied: {role} role cannot perform '{permission}'.")


def check_permission(actor, permission, activity=None):
    if not has_permission(actor, permission):
        _deny(actor, permission, activity)


def check_member_scope(actor, member_id, own_permission, staff_permission, activity=None):
    """Staff may act for anyone; a member may only act on their own records."""
    if has_permission(actor, staff_permission):
        return
    if has_permission(actor, own_permission) and actor.user_id == member_id:
        return
    _deny(actor, staff_permission if actor is None else own_permission, activity)


def _trailing_number(identifier):
    digits = ""
    for char in reversed(identifier):
        if not char.isdigit():
            break
        digits = char + digits
    return int(digits) if digits else 0


def next_number(ids):
    """Highest trailing number found in ids (like 'M003' or 'R-0042') plus one."""
    return max((_trailing_number(i) for i in ids), default=0) + 1


def audited(action, detail=None):
    """Decorator: log OK/FAIL to the activity log for a service method whose first argument is the actor."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, actor, *args, **kwargs):
            log = getattr(self, "activity", None)
            try:
                result = func(self, actor, *args, **kwargs)
            except PermissionDenied:
                raise
            except LibraryError as exc:
                if log:
                    log.log_actor(actor, action, "FAIL", str(exc))
                raise
            if log:
                log.log_actor(actor, action, "OK", detail(result) if detail else "")
            return result
        return wrapper
    return decorator


# --------------------------------------------------------------------------- settings
# key: (label, unit, minimum, maximum)
SETTING_META = {
    "borrow_limit": ("Borrow Limit", "books", 1, 20),
    "borrow_days": ("Duration", "days", 1, 90),
    "fine_rate": ("Fine Rate", "Rs / day", 0, 1000),
    "fine_limit": ("Fine Limit", "Rs", 0, 100000),
    "pickup_days": ("Pickup Window", "days", 1, 30),
    "extension_days": ("Extension Days", "days", 1, 60),
    "max_extensions": ("Max Extensions", "times", 0, 5),
    "lockout_attempts": ("Max Login Attempts", "tries", 1, 10),
    "lockout_minutes": ("Lockout Time", "minutes", 1, 1440),
}
DEFAULT_SETTINGS = {
    "borrow_limit": 3, "borrow_days": 14, "fine_rate": 5, "fine_limit": 100, "pickup_days": 2,
    "extension_days": 7, "max_extensions": 1, "lockout_attempts": 3, "lockout_minutes": 5,
}


class Settings:
    """Library rules that admins can change without touching code."""

    def __init__(self, storage=None, activity=None):
        self.storage = storage
        self.activity = activity
        self._values = dict(DEFAULT_SETTINGS)
        self.load()

    def load(self):
        self._values = dict(DEFAULT_SETTINGS)
        if self.storage is None:
            return
        stored = self.storage.read("settings", {})
        for key, value in stored.items():
            if key in SETTING_META and isinstance(value, int) and not isinstance(value, bool):
                _, _, low, high = SETTING_META[key]
                if low <= value <= high:
                    self._values[key] = value

    def get(self, key):
        return self._values[key]

    __getitem__ = get

    def keys(self):
        return list(SETTING_META)

    def rows(self):
        return [(n, SETTING_META[k][0], self._values[k], SETTING_META[k][1]) for n, k in enumerate(SETTING_META, 1)]

    @audited("SETTINGS_UPDATE", lambda r: f"{r[0]}={r[1]}")
    def update(self, actor, key, raw_value):
        check_permission(actor, "manage_settings", self.activity)
        if key not in SETTING_META:
            raise ValidationError(f"Unknown setting '{key}'.")
        label, _, low, high = SETTING_META[key]
        try:
            value = int(str(raw_value).strip())
        except ValueError:
            raise ValidationError(f"{label} must be a whole number.") from None
        if not low <= value <= high:
            raise ValidationError(f"{label} must be between {low} and {high}.")
        old = self._values[key]
        self._values[key] = value
        try:
            if self.storage is not None:
                self.storage.write("settings", self._values)
        except StorageError:
            self._values[key] = old
            raise
        return key, value


# --------------------------------------------------------------------------- activity log
class ActivityLog:
    """Pipe-separated activity log: timestamp|user|role|action|result|detail."""

    FIELDS = ("timestamp", "user", "role", "action", "result", "detail")

    def __init__(self, path, clock=datetime.now):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self._logger = logging.getLogger(f"smart_library.activity.{self.path.resolve()}")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        if not self._logger.handlers:
            handler = logging.FileHandler(self.path, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(handler)

    @staticmethod
    def _clean(value):
        return str(value).replace("|", "/").replace("\n", " ").strip()

    def log(self, user, role, action, result="OK", detail=""):
        stamp = self.clock().strftime("%Y-%m-%d %H:%M:%S")
        parts = [stamp, user, role, action, result, detail]
        self._logger.info("|".join([parts[0]] + [self._clean(p) for p in parts[1:]]))

    def log_actor(self, actor, action, result="OK", detail=""):
        user = actor.username if actor is not None else "system"
        role = actor.role.value if actor is not None else "-"
        self.log(user, role, action, result, detail)

    def read(self, user=None, day=None, action=None):
        """Yield matching entries, reading the file line by line."""
        if not self.path.exists():
            return
        with open(self.path, encoding="utf-8") as handle:
            for line in handle:
                parts = line.rstrip("\n").split("|", 5)
                if len(parts) < 5:
                    continue
                entry = dict(zip(self.FIELDS, parts + [""] * (6 - len(parts))))
                if user and entry["user"].lower() != user.lower():
                    continue
                if day and not entry["timestamp"].startswith(day):
                    continue
                if action and action.upper() not in entry["action"].upper():
                    continue
                yield entry

    def close(self):
        for handler in list(self._logger.handlers):
            handler.close()
            self._logger.removeHandler(handler)


# --------------------------------------------------------------------------- sample data
SAMPLE_BOOKS = [
    ("Clean Code", "Robert C. Martin", "Programming", 3),
    ("Python Crash Course", "Eric Matthes", "Programming", 3),
    ("Fluent Python", "Luciano Ramalho", "Programming", 2),
    ("Python Cookbook", "David Beazley", "Programming", 2),
    ("The Pragmatic Programmer", "Andrew Hunt", "Programming", 2),
    ("Introduction to Algorithms", "Thomas Cormen", "Programming", 2),
    ("Design Patterns", "Erich Gamma", "Programming", 1),
    ("Refactoring", "Martin Fowler", "Programming", 2),
    ("Automate the Boring Stuff", "Al Sweigart", "Programming", 3),
    ("Learning Python", "Mark Lutz", "Programming", 2),
    ("The Alchemist", "Paulo Coelho", "Fiction", 2),
    ("To Kill a Mockingbird", "Harper Lee", "Fiction", 2),
    ("1984", "George Orwell", "Fiction", 3),
    ("Pride and Prejudice", "Jane Austen", "Fiction", 2),
    ("The Great Gatsby", "F. Scott Fitzgerald", "Fiction", 2),
    ("The Kite Runner", "Khaled Hosseini", "Fiction", 1),
    ("Life of Pi", "Yann Martel", "Fiction", 2),
    ("Sapiens", "Yuval Noah Harari", "History", 2),
    ("Homo Deus", "Yuval Noah Harari", "History", 1),
    ("Guns, Germs, and Steel", "Jared Diamond", "History", 1),
    ("The Discovery of India", "Jawaharlal Nehru", "History", 2),
    ("A Brief History of Time", "Stephen Hawking", "Science", 2),
    ("Cosmos", "Carl Sagan", "Science", 2),
    ("The Selfish Gene", "Richard Dawkins", "Science", 1),
    ("Astrophysics for People in a Hurry", "Neil deGrasse Tyson", "Science", 2),
    ("Atomic Habits", "James Clear", "Self-Help", 3),
    ("The 7 Habits of Highly Effective People", "Stephen Covey", "Self-Help", 2),
    ("Deep Work", "Cal Newport", "Self-Help", 2),
    ("Man's Search for Meaning", "Viktor Frankl", "Self-Help", 1),
    ("Zero to One", "Peter Thiel", "Business", 2),
    ("The Lean Startup", "Eric Ries", "Business", 2),
    ("Good to Great", "Jim Collins", "Business", 1),
    ("Rich Dad Poor Dad", "Robert Kiyosaki", "Business", 2),
    ("The Hobbit", "J.R.R. Tolkien", "Fantasy", 2),
    ("Harry Potter and the Sorcerer's Stone", "J.K. Rowling", "Fantasy", 3),
    ("A Game of Thrones", "George R.R. Martin", "Fantasy", 2),
    ("The Name of the Wind", "Patrick Rothfuss", "Fantasy", 1),
    ("Wings of Fire", "A.P.J. Abdul Kalam", "Biography", 2),
    ("Steve Jobs", "Walter Isaacson", "Biography", 2),
    ("Long Walk to Freedom", "Nelson Mandela", "Biography", 1),
]

SAMPLE_USERS = [
    # user_id, username, full name, role, password
    ("U001", "admin", "Asha Rao", Role.ADMIN, "admin123"),
    ("U002", "lib1", "Karan Shah", Role.LIBRARIAN, "lib123"),
    ("M003", "riya", "Riya Patil", Role.MEMBER, "member123"),
    ("M004", "amit", "Amit Joshi", Role.MEMBER, "member123"),
    ("M005", "sneha", "Sneha Kulkarni", Role.MEMBER, "member123"),
    ("M006", "rohan", "Rohan Desai", Role.MEMBER, "member123"),
    ("M007", "neha", "Neha Verma", Role.MEMBER, "member123"),
]
