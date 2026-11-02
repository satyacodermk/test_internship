"""User & member management."""
from datetime import date

from config import (MIN_PASSWORD_LENGTH, NotFoundError, Role, ValidationError, audited, check_permission,
                    has_permission, next_number)
from models import Member, User, user_from_dict
from storage import PersistentCollection, UnitOfWork


class UserService(PersistentCollection):
    FILE = "users"

    def __init__(self, storage, activity, today=date.today):
        super().__init__(storage)
        self.activity = activity
        self.today = today
        self._users = {}
        self._by_username = {}
        self.borrowing = self.fines = self.reservations = None
        self.reload()

    def attach(self, borrowing, fines, reservations):
        self.borrowing, self.fines, self.reservations = borrowing, fines, reservations

    # -- persistence
    def _serialize(self):
        return [u.to_dict() for u in self._users.values()]

    def _load(self, raw):
        self._users = {}
        for row in raw:
            user = user_from_dict(row)
            self._users[user.user_id] = user
        self._by_username = {u.username.lower(): u for u in self._users.values()}

    # -- queries
    def find(self, user_id):
        return self._users.get(str(user_id).strip().upper())

    def get(self, user_id):
        user = self.find(user_id)
        if user is None:
            raise NotFoundError(f"User '{user_id}' was not found.")
        return user

    def get_member(self, member_id):
        user = self.get(member_id)
        if not isinstance(user, Member):
            raise ValidationError(f"{user.user_id} is not a library member.")
        return user

    def by_username(self, username):
        return self._by_username.get(str(username).strip().lower())

    def list_users(self, role=None):
        users = sorted(self._users.values(), key=lambda u: u.user_id)
        return [u for u in users if role is None or u.role is role]

    def members(self):
        return self.list_users(Role.MEMBER)

    def search(self, text, role=None):
        text = text.strip().lower()
        return [u for u in self.list_users(role)
                if text in " ".join([u.user_id, u.username, u.full_name, u.email]).lower()]

    # -- validation helpers
    @staticmethod
    def _check_password(password):
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValidationError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")

    @staticmethod
    def _check_email(email):
        if email and ("@" not in email or " " in email):
            raise ValidationError("Email address looks invalid.")

    @staticmethod
    def _check_phone(phone):
        if phone and not (phone.replace("+", "").replace("-", "").replace(" ", "").isdigit()):
            raise ValidationError("Phone number may only contain digits, spaces, '+' and '-'.")

    def _authorize_target(self, actor, target):
        if isinstance(target, Member) and (has_permission(actor, "manage_members") or has_permission(actor, "manage_users")):
            return
        check_permission(actor, "manage_users", self.activity)

    def _check_no_obligations(self, user, verb):
        if not isinstance(user, Member) or self.borrowing is None:
            return
        loans = self.borrowing.loans_for_member(user.user_id)
        if loans:
            raise ValidationError(f"Cannot {verb}: member has {len(loans)} active loan(s).")
        balance = self.fines.balance_for(user.user_id)
        if balance > 0:
            raise ValidationError(f"Cannot {verb}: member has unpaid fines of Rs {balance}.")

    def _active_admins(self):
        return [u for u in self._users.values() if u.role is Role.ADMIN and u.active]

    # -- commands
    @audited("USER_ADD", lambda u: f"{u.user_id} {u.username}")
    def add_user(self, actor, username, full_name, role, password, email="", phone=""):
        role = role if isinstance(role, Role) else Role(role)
        check_permission(actor, "register_member" if role is Role.MEMBER else "manage_users", self.activity)
        username, full_name = username.strip(), full_name.strip()
        if len(username) < 3 or not username.replace("_", "").isalnum():
            raise ValidationError("Username must be 3+ letters, digits or underscores.")
        if self.by_username(username):
            raise ValidationError(f"Username '{username}' is already taken.")
        if not full_name:
            raise ValidationError("Full name is required.")
        self._check_password(password)
        self._check_email(email.strip())
        self._check_phone(phone.strip())
        user_id = f"{'M' if role is Role.MEMBER else 'U'}{next_number(self._users):03d}"
        if role is Role.MEMBER:
            user = Member.create(user_id, username, full_name, password, role=role, email=email.strip(),
                                 phone=phone.strip(), joined_on=self.today().isoformat())
        else:
            user = User.create(user_id, username, full_name, password, role=role, email=email.strip())
        with UnitOfWork(self):
            self._users[user_id] = user
            self._by_username[username.lower()] = user
        return user

    @audited("USER_UPDATE", lambda u: u.user_id)
    def update_user(self, actor, user_id, full_name=None, email=None, phone=None, password=None):
        target = self.get(user_id)
        self._authorize_target(actor, target)
        with UnitOfWork(self):
            if full_name is not None:
                if not full_name.strip():
                    raise ValidationError("Full name cannot be empty.")
                target.full_name = full_name.strip()
            if email is not None:
                self._check_email(email.strip())
                target.email = email.strip()
            if phone is not None:
                if not isinstance(target, Member):
                    raise ValidationError("Only members have a phone number.")
                self._check_phone(phone.strip())
                target.phone = phone.strip()
            if password is not None:
                self._check_password(password)
                target.set_password(password)
        return target

    @audited("USER_DEACTIVATE", lambda u: u.user_id)
    def deactivate(self, actor, user_id):
        target = self.get(user_id)
        self._authorize_target(actor, target)
        if target.user_id == actor.user_id:
            raise ValidationError("You cannot deactivate your own account.")
        if not target.active:
            raise ValidationError(f"{target.user_id} is already inactive.")
        if target.role is Role.ADMIN and len(self._active_admins()) <= 1:
            raise ValidationError("Cannot deactivate the last active admin.")
        self._check_no_obligations(target, "deactivate")
        with UnitOfWork(self, self.reservations, self.reservations.books):
            target.active = False
            self.reservations.cancel_all_for_member(target.user_id)
        return target

    @audited("USER_ACTIVATE", lambda u: u.user_id)
    def activate(self, actor, user_id):
        target = self.get(user_id)
        self._authorize_target(actor, target)
        if target.active:
            raise ValidationError(f"{target.user_id} is already active.")
        with UnitOfWork(self):
            target.active = True
            target.failed_attempts, target.locked_until = 0, None
        return target

    @audited("USER_REMOVE", lambda u: u.user_id)
    def remove_user(self, actor, user_id):
        check_permission(actor, "manage_users", self.activity)
        target = self.get(user_id)
        if target.user_id == actor.user_id:
            raise ValidationError("You cannot remove your own account.")
        if target.role is Role.ADMIN and target.active and len(self._active_admins()) <= 1:
            raise ValidationError("Cannot remove the last active admin.")
        self._check_no_obligations(target, "remove")
        with UnitOfWork(self, self.reservations, self.reservations.books):
            self.reservations.cancel_all_for_member(target.user_id)
            del self._users[target.user_id]
            self._by_username.pop(target.username.lower(), None)
        return target
