"""Service layer + the container that wires everything together."""
from datetime import date, datetime
from pathlib import Path

import config
from config import ActivityLog, Settings
from services.auth_service import AuthService
from services.book_service import BookService
from services.borrowing_service import BorrowingService
from services.fine_service import FineService
from services.report_service import ReportService
from services.reservation_service import ReservationService
from services.user_service import UserService
from storage import BackupService, JsonStorage


class ServiceContainer:
    """Creates storage, backups, logging and every service (dependency injection keeps tests simple)."""

    def __init__(self, base_dir=None, seed=True, today=date.today, now=datetime.now):
        base = Path(base_dir) if base_dir else config.BASE_DIR
        self.today, self.now = today, now
        self.activity = ActivityLog(base / "logs" / config.LOG_FILE_NAME, clock=now)
        self.storage = JsonStorage(base / "data", seed=seed)
        self.backup = BackupService(base / "data", base / "backups", validator=self.storage.validate,
                                    activity=self.activity, now=now)
        self.startup_report = self.storage.initialize(self.backup)
        self.settings = Settings(self.storage, self.activity)
        self.users = UserService(self.storage, self.activity, today)
        self.books = BookService(self.storage, self.activity)
        self.fines = FineService(self.storage, self.activity, self.settings, today)
        self.reservations = ReservationService(self.storage, self.activity, self.settings, self.books, today)
        self.borrowing = BorrowingService(self.storage, self.activity, self.settings, self.users, self.books,
                                          self.fines, self.reservations, today)
        self.users.attach(self.borrowing, self.fines, self.reservations)
        self.books.attach(self.borrowing, self.reservations)
        self.reservations.attach(self.users, self.borrowing)
        self.reports = ReportService(self.activity, self.settings, self.users, self.books, self.borrowing,
                                     self.reservations, self.fines, today)
        self.auth = AuthService(self.users, self.activity, self.settings, now)

    def startup(self):
        """Expire old holds, take a start-up backup and write the start-up log entry."""
        expired = self.reservations.expire_overdue()
        backup_name = self.backup.create_backup("startup")
        counts = {"books": len(self.books.list_all()), "users": len(self.users.list_users())}
        self.activity.log("system", "-", "STARTUP", "OK", f"{counts['books']} books, {counts['users']} users")
        return {"expired": len(expired), "backup": backup_name, **counts}

    def auto_backup(self, reason):
        return self.backup.create_backup(reason)

    def reload_all(self):
        """Re-read every data file (used after restoring a backup)."""
        self.settings.load()
        for service in (self.users, self.books, self.fines, self.reservations, self.borrowing):
            service.reload()

    def shutdown(self):
        self.activity.log("system", "-", "SHUTDOWN", "OK")
        self.activity.close()
