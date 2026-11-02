import unittest

from config import AuthError, PermissionDenied, Role
from tests import LibraryTestCase


class AuthTests(LibraryTestCase):
    def test_successful_login_and_logout(self):
        user = self.app.auth.login("RIYA", "riya123")
        self.assertEqual(user.user_id, self.riya.user_id)
        self.assertIsNotNone(user.last_login)
        self.app.auth.logout(user)
        actions = [e["action"] for e in self.app.activity.read()]
        self.assertIn("LOGIN", actions)
        self.assertIn("LOGOUT", actions)

    def test_wrong_password_is_rejected_and_logged(self):
        with self.assertRaises(AuthError) as ctx:
            self.app.auth.login("riya", "wrong")
        self.assertEqual(ctx.exception.attempts_left, 2)
        self.assertTrue(list(self.app.activity.read(action="LOGIN_FAILED")))

    def test_unknown_user_is_rejected(self):
        with self.assertRaises(AuthError):
            self.app.auth.login("ghost", "whatever")

    def test_account_locks_after_too_many_failures(self):
        for _ in range(3):
            with self.assertRaises(AuthError):
                self.app.auth.login("riya", "bad")
        with self.assertRaises(AuthError) as ctx:
            self.app.auth.login("riya", "riya123")  # right password but locked
        self.assertIn("locked", str(ctx.exception))
        self.clock.advance(minutes=10)
        self.assertEqual(self.app.auth.login("riya", "riya123").username, "riya")

    def test_inactive_account_cannot_login(self):
        self.app.users.deactivate(self.admin, self.neha.user_id)
        with self.assertRaises(AuthError):
            self.app.auth.login("neha", "neha123")

    def test_passwords_are_salted_hashes(self):
        stored = self.app.storage.read("users")
        riya, amit = (u for u in stored if u["username"] in ("riya", "amit"))
        self.assertNotIn("riya123", str(riya))
        self.assertNotEqual(riya["salt"], amit["salt"])
        self.assertEqual(len(riya["password_hash"]), 64)

    def test_rbac_member_cannot_use_admin_features(self):
        self.assertFalse(self.app.auth.authorize(self.riya, "manage_users"))
        self.assertTrue(list(self.app.activity.read(action="ACCESS_DENIED")))
        with self.assertRaises(PermissionDenied):
            self.app.users.add_user(self.riya, "hacker", "Evil", Role.ADMIN, "password1")

    def test_librarian_cannot_change_settings_but_admin_can(self):
        with self.assertRaises(PermissionDenied):
            self.app.settings.update(self.lib, "borrow_limit", 5)
        self.app.settings.update(self.admin, "borrow_limit", 5)
        self.assertEqual(self.app.settings.get("borrow_limit"), 5)

    def test_duplicate_username_and_weak_password(self):
        from config import ValidationError
        with self.assertRaises(ValidationError):
            self.app.users.add_user(self.lib, "RIYA", "Other", Role.MEMBER, "password1")
        with self.assertRaises(ValidationError):
            self.app.users.add_user(self.lib, "newbie", "New", Role.MEMBER, "123")

    def test_settings_validation(self):
        from config import ValidationError
        for bad in ("abc", "-1", "0", "9999", "2.5"):
            with self.assertRaises(ValidationError):
                self.app.settings.update(self.admin, "borrow_limit", bad)


if __name__ == "__main__":
    unittest.main()
