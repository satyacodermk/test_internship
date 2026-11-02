"""Admin dashboard: users, books, settings, reports, logs and backups."""
from collections import deque

from cli.display import (STOP, Menu, MenuItem, ask, ask_password, banner, confirm, error, info, money, paginate,
                         section, stat_strip, success, table, warn)
from cli.librarian_menu import inventory_screen, reports_screen
from config import LibraryError, Role, ValidationError

ROLE_CHOICES = {"1": Role.ADMIN, "2": Role.LIBRARIAN, "3": Role.MEMBER}


def _user_rows(users):
    return [[u.user_id, u.username, u.full_name, u.role.value, "Active" if u.active else "Inactive"] for u in users]


def user_management_screen(app, user):
    headers = ["ID", "Username", "Name", "Role", "Status"]

    def add():
        role = ROLE_CHOICES.get(ask("Role: [1] Admin  [2] Librarian  [3] Member"))
        if role is None:
            raise ValidationError("Please choose 1, 2 or 3.")
        name, username = ask("Full name"), ask("Username")
        email = ask("Email (optional)", default="")
        phone = ask("Phone (optional, members only)", default="") if role is Role.MEMBER else ""
        password = ask_password("Password")
        if ask_password("Confirm password") != password:
            raise ValidationError("Passwords do not match.")
        created = app.users.add_user(user, username, name, role, password, email=email, phone=phone)
        success(f"User created: {created.user_id} ({created.role.value})")

    def update():
        target = app.users.get(ask("Enter user ID to update"))
        info("Leave a field blank to keep its current value.")
        name = ask(f"Full name [{target.full_name}]", default="")
        email = ask(f"Email [{target.email}]", default="")
        password = ask_password("New password (blank = keep)")
        app.users.update_user(user, target.user_id, full_name=name or None, email=email or None,
                              password=password or None)
        success("User updated.")

    def search():
        found = app.users.search(ask("Search text"))
        table(headers, _user_rows(found)) if found else info("No users found.")

    def deactivate_or_remove():
        target = app.users.get(ask("User ID"))
        choice = ask("[1] Deactivate  [2] Reactivate  [3] Remove permanently")
        if choice == "1":
            app.users.deactivate(user, target.user_id)
            success(f"{target.user_id} deactivated.")
        elif choice == "2":
            app.users.activate(user, target.user_id)
            success(f"{target.user_id} reactivated.")
        elif choice == "3":
            if confirm(f"Permanently remove {target.full_name} ({target.user_id})?"):
                app.users.remove_user(user, target.user_id)
                app.auto_backup("user removed")
                success("User removed.")
        else:
            error("Please choose 1, 2 or 3.")

    return Menu("USER MANAGEMENT", [
        MenuItem("1", "Add User", "manage_users", add),
        MenuItem("2", "Update User", "manage_users", update),
        MenuItem("3", "Search User", "manage_users", search),
        MenuItem("4", "Deactivate/Remove", "manage_users", deactivate_or_remove),
        MenuItem("5", "List All Users", "manage_users", lambda: paginate(headers, _user_rows(app.users.list_users()))),
        MenuItem("6", "Back", None, lambda: STOP)], user, app.auth).run()


def settings_screen(app, user):
    keys = app.settings.keys()
    while True:
        section("LIBRARY SETTINGS")
        table(["#", "Setting", "Value"], [[n, label, f"{value} {unit}"] for n, label, value, unit in app.settings.rows()])
        raw = ask("Enter setting number to change (blank to go back)", default="")
        if not raw:
            return
        if not raw.isdigit() or not 1 <= int(raw) <= len(keys):
            error("Invalid setting number.")
            continue
        key = keys[int(raw) - 1]
        value = ask("New value")
        if not confirm("Save this change?"):
            info("Cancelled - nothing was changed.")
            continue
        try:
            app.settings.update(user, key, value)
        except LibraryError as exc:
            error(str(exc))
            continue
        app.auto_backup("settings changed")
        success("Settings saved. New rules apply to new transactions; existing due dates are unchanged.")


def _show_entries(entries):
    paginate(["Time", "User", "Role", "Action", "Result"],
             [[e["timestamp"][5:16], e["user"], e["role"], e["action"], e["result"]] for e in entries])


def activity_log_screen(app, user):
    return Menu("ACTIVITY LOG", [
        MenuItem("1", "Last 20 entries", "view_logs", lambda: _show_entries(deque(app.activity.read(), maxlen=20))),
        MenuItem("2", "Filter by user", "view_logs", lambda: _show_entries(list(app.activity.read(user=ask("Username"))))),
        MenuItem("3", "Filter by date", "view_logs",
                 lambda: _show_entries(list(app.activity.read(day=ask("Date (YYYY-MM-DD)"))))),
        MenuItem("4", "Filter by action", "view_logs",
                 lambda: _show_entries(list(app.activity.read(action=ask("Action keyword (e.g. LOGIN_FAILED, BORROW)"))))),
        MenuItem("5", "Back", None, lambda: STOP)], user, app.auth).run()


def backup_screen(app, user):
    def show_list():
        backups = app.backup.list_backups()
        table(["Backup", "Size", "Status"], [[b["name"], f"{max(1, round(b['size'] / 1024))} KB",
                                              "Valid" if b["valid"] else "DAMAGED"] for b in backups]) \
            if backups else info("No backups yet.")

    def create():
        name = app.backup.create_backup("manual")
        app.activity.log_actor(user, "BACKUP", "OK", name)
        success(f"Backup created: {name}")

    def restore():
        show_list()
        name = ask("Backup name to restore")
        warn("Restoring will replace current data (a safety backup is taken first).")
        if ask("Type YES to continue", default="") != "YES":
            return info("Restore cancelled.")
        app.backup.restore_backup(name)
        app.reload_all()
        app.activity.log_actor(user, "RESTORE", "OK", name)
        success(f"Data restored from {name}.")

    return Menu("BACKUP & RECOVERY", [
        MenuItem("1", "Create Backup Now", "manage_backups", create),
        MenuItem("2", "List Backups", "manage_backups", show_list),
        MenuItem("3", "Restore a Backup", "manage_backups", restore),
        MenuItem("4", "Back", None, lambda: STOP)], user, app.auth).run()


class AdminMenu:
    REQUIRED_PERMISSION = "manage_users"

    def __init__(self, app, user):
        self.app, self.user = app, user

    def header(self):
        stats = self.app.reports.dashboard_stats()
        banner(f"ADMIN DASHBOARD - Welcome, {self.user.full_name}")
        print(f"Last login: {(self.user.previous_login or 'first login').replace('T', ' ')[:16]}")
        stat_strip([(stats["users"], "Users"), (stats["books"], "Books"),
                    (stats["active_loans"], "Active Loans"), (stats["overdue"], "Overdue")])

    def run(self):
        app, user = self.app, self.user
        if not app.auth.authorize(user, self.REQUIRED_PERMISSION):
            error("Access denied: this dashboard is for admins only.")
            return
        Menu("Admin", [
            MenuItem("1", "Manage Users", "manage_users", lambda: user_management_screen(app, user)),
            MenuItem("2", "Manage Books", "manage_books", lambda: inventory_screen(app, user)),
            MenuItem("3", "Library Settings", "manage_settings", lambda: settings_screen(app, user)),
            MenuItem("4", "Reports", "view_all_reports", lambda: reports_screen(app, user)),
            MenuItem("5", "Activity Logs", "view_logs", lambda: activity_log_screen(app, user)),
            MenuItem("6", "Backup & Recovery", "manage_backups", lambda: backup_screen(app, user)),
            MenuItem("7", "Logout", None, lambda: STOP)], user, app.auth, header=self.header).run()
