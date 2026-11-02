"""Authentication, lock-out and permission checks."""
from datetime import datetime, timedelta

from config import AuthError, has_permission
from storage import UnitOfWork


class AuthService:
    def __init__(self, users, activity, settings, now=datetime.now):
        self.users, self.activity, self.settings, self.now = users, activity, settings, now
        self._unknown_attempts = {}

    def _attempts_left(self, count):
        return max(0, self.settings.get("lockout_attempts") - count)

    def login(self, username, password):
        key = username.strip().lower()
        now = self.now()
        user = self.users.by_username(key)
        if user is None:
            count = self._unknown_attempts[key] = self._unknown_attempts.get(key, 0) + 1
            self.activity.log(username or "?", "-", "LOGIN_FAILED", "FAIL", "unknown user")
            raise AuthError("Invalid credentials.", self._attempts_left(count))
        if user.is_locked(now):
            self.activity.log_actor(user, "LOGIN_FAILED", "FAIL", "account locked")
            until = datetime.fromisoformat(user.locked_until).strftime("%H:%M")
            raise AuthError(f"Account is locked until {until}. Please try again later.", 0)
        if not user.verify_password(password):
            with UnitOfWork(self.users):
                user.failed_attempts += 1
                left = self._attempts_left(user.failed_attempts)
                if left == 0:
                    user.locked_until = (now + timedelta(minutes=self.settings.get("lockout_minutes"))).isoformat(timespec="seconds")
                    user.failed_attempts = 0
            self.activity.log_actor(user, "LOGIN_FAILED", "FAIL", f"attempts left {left}")
            if left == 0:
                self.activity.log_actor(user, "ACCOUNT_LOCKED", "FAIL", f"{self.settings.get('lockout_minutes')} min")
            raise AuthError("Invalid credentials.", left)
        if not user.is_active:
            self.activity.log_actor(user, "LOGIN_FAILED", "FAIL", "inactive account")
            raise AuthError("This account is inactive. Please contact the library.", None)
        with UnitOfWork(self.users):
            user.failed_attempts, user.locked_until = 0, None
            user.previous_login, user.last_login = user.last_login, now.isoformat(timespec="seconds")
        self._unknown_attempts.pop(key, None)
        self.activity.log_actor(user, "LOGIN", "OK")
        return user

    def logout(self, user):
        self.activity.log_actor(user, "LOGOUT", "OK")

    @staticmethod
    def has(user, permission):
        return has_permission(user, permission)

    def authorize(self, user, permission):
        """True if allowed; otherwise logs ACCESS_DENIED and returns False."""
        if has_permission(user, permission):
            return True
        self.activity.log_actor(user, "ACCESS_DENIED", "FAIL", permission)
        return False
