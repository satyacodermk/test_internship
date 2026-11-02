"""Start-up screen and login screen."""
from cli.admin_menu import AdminMenu
from cli.display import (ask, ask_password, banner, error, info, paint, pause, success, warn)
from cli.librarian_menu import LibrarianMenu
from cli.member_menu import MemberMenu
from config import APP_NAME, APP_VERSION, AuthError, Role

DASHBOARDS = {Role.ADMIN: AdminMenu, Role.LIBRARIAN: LibrarianMenu, Role.MEMBER: MemberMenu}


def show_startup(app, summary):
    banner(f"{APP_NAME}  v{APP_VERSION}", 40)
    print()
    for entry in app.startup_report:
        if entry["status"] == "OK":
            print(f"[{paint(' OK ', 'green')}] {entry['file']}")
        elif entry["status"] == "Restored from backup":
            print(f"[{paint('WARN', 'yellow')}] {entry['detail']}")
        else:
            print(f"[{paint('NEW ', 'cyan')}] {entry['detail']}")
    print(f"\nExpired reservations cleaned: {summary['expired']}")
    print(f"Data loaded: {summary['books']} books, {summary['users']} users.")
    print(f"Start-up backup created: {summary['backup']}")
    pause("Press Enter to continue to login")


class AuthMenu:
    def __init__(self, app):
        self.app = app

    def run(self):
        while True:
            banner(APP_NAME)
            print("1. Login\n2. Exit")
            choice = ask("Choose an option")
            if choice == "1":
                user = self.login()
                if user:
                    self.open_dashboard(user)
            elif choice == "2":
                return
            else:
                error("Invalid choice. Enter 1 or 2.")

    def login(self):
        username = ask("Username")
        password = ask_password("Password")
        try:
            user = self.app.auth.login(username, password)
        except AuthError as exc:
            if exc.attempts_left is None:
                error(str(exc))
            elif exc.attempts_left == 0 and "locked" in str(exc).lower():
                error(str(exc))
            else:
                error(f"{exc} Attempts left: {exc.attempts_left}")
            return None
        success(f"Login successful. Welcome, {user.full_name.split()[0]} ({user.role.value})!")
        return user

    def open_dashboard(self, user):
        DASHBOARDS[user.role](self.app, user).run()
        self.app.auth.logout(user)
        info("You have been logged out.")
