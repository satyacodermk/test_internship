"""Member dashboard: members only ever see their own records."""
from cli.display import (STOP, Menu, MenuItem, ask, banner, confirm, error, info, money, paginate, stat_strip,
                         success, table, warn)
from cli.librarian_menu import (_reserve, my_loans_table, print_receipt, print_return, return_screen,
                                search_screen)
from config import BookUnavailable


class MemberMenu:
    REQUIRED_PERMISSION = "borrow_self"

    def __init__(self, app, user):
        self.app, self.user = app, user

    def header(self):
        summary = self.app.reports.member_summary(self.user.user_id)
        banner(f"Hello, {self.user.full_name.split()[0]}!")
        stat_strip([(f"{summary['borrowed']}/{summary['limit']}", "Borrowed"),
                    (summary["reservations"], "Reservations"), (money(summary["fines"]), "Fines")])
        for txn in summary["overdue"]:
            error(f"{txn.txn_id} is OVERDUE - please return it.")
        for txn in summary["due_soon"]:
            warn(f"{txn.txn_id} is due on {txn.due_date}.")
        if summary["fines"] and summary["fines"] >= 0.8 * self.app.settings.get("fine_limit"):
            warn("Your fines are close to the borrowing limit.")

    # -- screens
    def borrow(self):
        app, user = self.app, self.user
        book = app.books.resolve(ask("Book ID / ISBN"))
        print(f"{book.book_id}  {book.title} - {book.author}  ({book.available_copies}/{book.total_copies} available)")
        try:
            print_receipt(app.borrowing.issue_book(user, user.user_id, book.book_id))
        except BookUnavailable as exc:
            error(str(exc))
            if confirm("Reserve it instead?"):
                _reserve(app, user, user.user_id, book)

    def reserve(self):
        _reserve(self.app, self.user, self.user.user_id, self.app.books.resolve(ask("Book ID / ISBN")))

    def my_books(self):
        app, user = self.app, self.user
        if not my_loans_table(app, user.user_id):
            return
        txn_id = ask("Enter transaction ID to extend or return (blank to go back)", default="")
        if not txn_id:
            return
        action = ask("[E]xtend or [R]eturn", default="E").upper()
        if action == "R":
            print_return(app, app.borrowing.return_book(user, txn_id))
        else:
            txn = app.borrowing.extend(user, txn_id)
            success(f"Extended {txn.txn_id}. New due date: {txn.due_date}")

    def extension(self):
        if not my_loans_table(self.app, self.user.user_id):
            return
        txn = self.app.borrowing.extend(self.user, ask("Transaction ID to extend"))
        success(f"Extended {txn.txn_id}. New due date: {txn.due_date}")

    def my_reservations(self):
        app, user = self.app, self.user
        items = app.reservations.for_member(user.user_id, active_only=True)
        if not items:
            return info("You have no active reservations.")
        rows = []
        for r in items:
            book = app.books.find(r.book_id)
            position = app.reservations.position_of(r)
            rows.append([r.reservation_id, book.title if book else r.book_id, r.status.value,
                         f"#{position}" if position else "-", str(r.pickup_by or "-")])
        table(["Reservation", "Book", "Status", "Queue", "Pickup By"], rows)
        rid = ask("Enter reservation ID to cancel (blank to go back)", default="")
        if rid:
            app.reservations.cancel(user, rid)
            success("Reservation cancelled.")

    def history(self):
        app, today = self.app, self.app.today()
        rows = []
        for t in app.borrowing.history_for_member(self.user.user_id):
            book = app.books.find(t.book_id)
            if t.is_active:
                status = f"OVERDUE {t.days_overdue(today)}d" if t.is_overdue(today) else "Active"
            else:
                status = f"Returned late ({t.days_late}d)" if t.days_late else "Returned"
            rows.append([t.borrowed_on, book.title if book else t.book_id, "Borrowed", status])
        paginate(["Date", "Book", "Action", "Status"], rows) if rows else info("No borrowing history yet.")

    def fines(self):
        app, user = self.app, self.user
        fines = app.fines.fines_for_member(user.user_id)
        if fines:
            table(["Txn", "Late", "Amount", "Paid", "Balance"],
                  [[f.txn_id, f"{f.days_late}d", money(f.amount), money(f.paid), money(f.balance)] for f in fines])
        accrued = app.borrowing.member_accrued(user.user_id)
        if accrued:
            warn(f"Fines building up on overdue loans: {money(accrued)}")
        print(f"Outstanding fines: {money(app.fines.balance_for(user.user_id))}")

    def run(self):
        app, user = self.app, self.user
        if not app.auth.authorize(user, self.REQUIRED_PERMISSION):
            error("Access denied: this dashboard is for members only.")
            return
        Menu("Member", [
            MenuItem("1", "Browse Books", "search_books", lambda: search_screen(app, user)),
            MenuItem("2", "Borrow", "borrow_self", self.borrow),
            MenuItem("3", "Reserve", "reserve_self", self.reserve),
            MenuItem("4", "Return", "return_self", lambda: return_screen(app, user)),
            MenuItem("5", "My Borrowed Books", "view_own", self.my_books),
            MenuItem("6", "My Reservations", "view_own", self.my_reservations),
            MenuItem("7", "My History", "view_own", self.history),
            MenuItem("8", "My Fines", "view_own", self.fines),
            MenuItem("9", "Request Extension", "extend_self", self.extension),
            MenuItem("0", "Logout", None, lambda: STOP)], user, app.auth, header=self.header).run()
