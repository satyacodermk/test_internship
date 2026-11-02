"""Reports and analytics."""
from collections import Counter
from datetime import date

from config import ValidationError, check_permission

REPORT_PERMISSIONS = {
    "inventory": "view_ops_reports", "loans": "view_ops_reports", "overdue": "view_ops_reports",
    "top_books": "view_all_reports", "active_members": "view_all_reports",
    "fines": "view_all_reports", "monthly": "view_all_reports",
}
REPORT_TITLES = {
    "inventory": "Inventory", "loans": "Current Loans", "top_books": "Most Borrowed Books",
    "active_members": "Most Active Members", "overdue": "Overdue Report", "fines": "Fine Summary",
    "monthly": "Monthly Statistics",
}


class ReportService:
    def __init__(self, activity, settings, users, books, borrowing, reservations, fines, today=date.today):
        self.activity, self.settings = activity, settings
        self.users, self.books, self.borrowing = users, books, borrowing
        self.reservations, self.fines, self.today = reservations, fines, today

    # -- helpers
    def _title(self, book_id):
        book = self.books.find(book_id)
        return book.title if book else "(deleted book)"

    def _member_name(self, member_id):
        user = self.users.find(member_id)
        return user.full_name if user else "(removed user)"

    # -- dashboards
    def dashboard_stats(self):
        return {"users": len(self.users.list_users()), "books": len(self.books.list_all()),
                "active_loans": len(self.borrowing.active_loans()), "overdue": len(self.borrowing.overdue_loans())}

    def desk_summary(self):
        today = self.today()
        return {"due_today": sum(1 for t in self.borrowing.active_loans() if t.due_date == today),
                "overdue": len(self.borrowing.overdue_loans()),
                "ready": len(self.reservations.ready_reservations())}

    def member_summary(self, member_id):
        today, loans = self.today(), self.borrowing.loans_for_member(member_id)
        return {"borrowed": len(loans), "limit": self.settings.get("borrow_limit"),
                "reservations": len(self.reservations.for_member(member_id, active_only=True)),
                "fines": self.fines.balance_for(member_id) + self.borrowing.member_accrued(member_id),
                "due_soon": [t for t in loans if 0 <= t.days_left(today) <= 2],
                "overdue": [t for t in loans if t.is_overdue(today)]}

    # -- reports (each returns title, summary cards, table headers/rows, optional chart/footer)
    def generate(self, actor, name):
        if name not in REPORT_PERMISSIONS:
            raise ValidationError(f"Unknown report '{name}'.")
        check_permission(actor, REPORT_PERMISSIONS[name], self.activity)
        report = getattr(self, f"_report_{name}")()
        report["title"] = REPORT_TITLES[name]
        self.activity.log_actor(actor, "REPORT", "OK", name)
        return report

    def _report_inventory(self):
        books = self.books.list_all()
        total = sum(b.total_copies for b in books)
        free = sum(b.available_copies for b in books)
        return {"summary": [(len(books), "Titles"), (total, "Total Copies"), (free, "Available"), (total - free, "Out/Held")],
                "headers": ["ID", "ISBN", "Title", "Author", "Category", "Avail"],
                "rows": [[b.book_id, b.isbn, b.title, b.author, b.category, f"{b.available_copies}/{b.total_copies}"]
                         for b in books]}

    def _report_loans(self):
        today = self.today()
        loans = sorted(self.borrowing.active_loans(), key=lambda t: t.due_date)
        rows = [[t.txn_id, t.member_id, self._title(t.book_id), t.borrowed_on, t.due_date,
                 f"OVERDUE {t.days_overdue(today)}d" if t.is_overdue(today) else f"{t.days_left(today)} days left"]
                for t in loans]
        return {"summary": [(len(loans), "Active Loans"), (len(self.borrowing.overdue_loans()), "Overdue")],
                "headers": ["Txn", "Member", "Title", "Borrowed", "Due", "Status"], "rows": rows}

    def _report_top_books(self, limit=10):
        counts = Counter(t.book_id for t in self.borrowing.all_transactions()).most_common(limit)
        return {"summary": [(sum(c for _, c in counts), "Borrows (top)")],
                "headers": ["Rank", "Title", "Borrowed"],
                "rows": [[rank, self._title(book_id), count] for rank, (book_id, count) in enumerate(counts, 1)]}

    def _report_active_members(self, limit=10):
        counts = Counter(t.member_id for t in self.borrowing.all_transactions()).most_common(limit)
        return {"summary": [(len(self.users.members()), "Members")],
                "headers": ["Rank", "Member", "Name", "Borrowed"],
                "rows": [[rank, mid, self._member_name(mid), count] for rank, (mid, count) in enumerate(counts, 1)]}

    def _report_overdue(self):
        today = self.today()
        rows, accrued = [], 0
        for txn in self.borrowing.overdue_loans():
            fine = self.borrowing.accrued_fine(txn)
            accrued += fine
            rows.append([txn.txn_id, txn.member_id, self._title(txn.book_id),
                         f"{txn.days_overdue(today)} days late", f"Fine: Rs {fine}"])
        recorded = self.fines.total_outstanding()
        return {"summary": [(len(rows), "Overdue Loans"), (f"Rs {accrued}", "Accrued Fines")],
                "headers": ["Txn", "Member", "Title", "Late", "Fine"], "rows": rows,
                "footer": [f"Total outstanding fines: Rs {recorded + accrued}"]}

    def _report_fines(self):
        fines = self.fines._fines
        charged, paid = sum(f.amount for f in fines), sum(f.paid for f in fines)
        per_member = {}
        for fine in fines:
            row = per_member.setdefault(fine.member_id, [0, 0])
            row[0] += fine.amount
            row[1] += fine.paid
        rows = [[mid, self._member_name(mid), f"Rs {a}", f"Rs {p}", f"Rs {a - p}"]
                for mid, (a, p) in sorted(per_member.items())]
        return {"summary": [(f"Rs {charged}", "Charged"), (f"Rs {paid}", "Collected"), (f"Rs {charged - paid}", "Outstanding")],
                "headers": ["Member", "Name", "Charged", "Paid", "Balance"], "rows": rows}

    def _report_monthly(self, months=6):
        today = self.today()
        year, month, slots = today.year, today.month, []
        for _ in range(months):
            slots.append((year, month))
            year, month = (year - 1, 12) if month == 1 else (year, month - 1)
        slots.reverse()
        counts = Counter((t.borrowed_on.year, t.borrowed_on.month) for t in self.borrowing.all_transactions())
        chart = [(date(y, m, 1).strftime("%b %Y"), counts[(y, m)]) for y, m in slots]
        return {"summary": [(sum(v for _, v in chart), f"Borrows (last {months} months)")], "chart": chart}
