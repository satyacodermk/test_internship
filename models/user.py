"""User model: salted PBKDF2 password hashing and role helpers."""
import hashlib
import hmac
import secrets
from datetime import datetime

import config
from config import Role


class User:
    """A staff account (Admin or Librarian). Members inherit from this class."""

    ID_PREFIX = "U"
    DEFAULT_ROLE = Role.ADMIN

    def __init__(self, user_id, username, full_name, role=None, password_hash="", salt="", email="",
                 active=True, created_at=None, last_login=None, previous_login=None,
                 failed_attempts=0, locked_until=None, iterations=None):
        self._user_id = user_id
        self.username = username
        self.full_name = full_name
        self.role = role or self.DEFAULT_ROLE
        self._password_hash = password_hash
        self._salt = salt
        self._iterations = iterations or config.HASH_ITERATIONS
        self.email = email
        self.active = active
        self.created_at = created_at or datetime.now().isoformat(timespec="seconds")
        self.last_login = last_login
        self.previous_login = previous_login
        self.failed_attempts = failed_attempts
        self.locked_until = locked_until

    # -- encapsulated identity / secrets
    @property
    def user_id(self):
        return self._user_id

    @property
    def password_hash(self):
        return self._password_hash

    @property
    def is_active(self):
        return self.active

    # -- passwords
    @staticmethod
    def hash_password(password, salt=None, iterations=None):
        salt = salt or secrets.token_hex(16)
        iterations = iterations or config.HASH_ITERATIONS
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
        return digest.hex(), salt

    def set_password(self, password):
        self._iterations = config.HASH_ITERATIONS
        self._password_hash, self._salt = self.hash_password(password, None, self._iterations)

    def verify_password(self, password):
        try:
            digest, _ = self.hash_password(password, self._salt, self._iterations)
        except ValueError:
            return False
        return hmac.compare_digest(digest, self._password_hash)

    # -- helpers
    def can(self, permission):
        return permission in config.PERMISSIONS[self.role]

    def is_locked(self, now):
        return bool(self.locked_until) and now < datetime.fromisoformat(self.locked_until)

    @classmethod
    def create(cls, user_id, username, full_name, password, role=None, **kwargs):
        user = cls(user_id, username, full_name, role or cls.DEFAULT_ROLE, **kwargs)
        user.set_password(password)
        return user

    # -- persistence
    def to_dict(self):
        return {
            "user_id": self._user_id, "username": self.username, "full_name": self.full_name,
            "role": self.role.value, "email": self.email, "active": self.active,
            "created_at": self.created_at, "last_login": self.last_login,
            "previous_login": self.previous_login, "failed_attempts": self.failed_attempts,
            "locked_until": self.locked_until, "password_hash": self._password_hash,
            "salt": self._salt, "iterations": self._iterations,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            data["user_id"], data["username"], data.get("full_name", data["username"]), Role(data["role"]),
            data["password_hash"], data["salt"], email=data.get("email", ""), active=data.get("active", True),
            created_at=data.get("created_at"), last_login=data.get("last_login"),
            previous_login=data.get("previous_login"), failed_attempts=data.get("failed_attempts", 0),
            locked_until=data.get("locked_until"), iterations=data.get("iterations", 100_000))

    def __repr__(self):
        return f"<{type(self).__name__} {self._user_id} {self.username} ({self.role.value})>"
