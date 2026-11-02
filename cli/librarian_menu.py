"""Librarian dashboard plus the desk screens that Admin and Member menus reuse."""
from cli.display import (NAV, STOP, Menu, MenuItem, ask, ask_int, ask_password, banner, bar_chart, confirm, error,
                         info, money, paginate, section, stat_strip, success, table, warn)
from config import BookUnavailable, NotFoundError, Role, ValidationError
from services.report_service import REPORT_PERMISSIONS, REPORT_TITLES


# ------------------------------------------------------------------ shared helpers
def show_books(books):
    table(["ID", "ISBN", "Title", "Author", "Category", "Avail"],
          [[b.book_id, b.isbn, b.title, b.author, b.category, f"{b.available_copies}/{b.total_copies}"] for b in books],
          "lllllr")


def _optional_int(raw):
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise ValidationError("Total copies must be a whole number.") from None


def print_receipt(result):
    txn, member, book = result["txn"], result["member"], result["book"]
    print(f"\nTransaction ID : {txn.txn_id}")
    print(f"Member         : {member.full_name} ({member.user_id})")
    print(f"Book           : {book.title} ({book.book_id})")
    print(f"Borrowed On    : {txn.borrowed_on}")
    print(f"Due Date       : {txn.due_date}")
    success(f"Book borrowed successfully. Copies left: {result['copies_left']}")


def print_return(app, result):
    txn, fine = result["txn"], result["fine"]
    table(["Field", "Value"], [["Transaction", txn.txn_id], ["Book", result["book"].title],
                               ["Borrowed On", txn.borrowed_on], ["Due", txn.due_date],
                               ["Returned", txn.returned_on], ["Days Late", result["days_late"]]])
    if fine:
        warn(f"Returned {result['days_late']} days late. Fine: {money(fine.amount)}.")
    else:
        success("Returned on time.")
    nxt = result["next_reservation"]
    if nxt:
        member = app.users.find(nxt.member_id)
        info(f"Copy reserved for next member: {nxt.member_id} ({member.full_name if member else '?'}).")
        info(f"Pickup by {nxt.pickup_by}")


def reservation_rows(app, reservations, with_book=False):
    rows = []
    for position, item in enumerate(reservations, 1):
        member = app.users.find(item.member_id)
        row = [item.reservation_id, f"{item.member_id} {member.full_name if member else '?'}", item.status.value,
               str(item.pickup_by) if item.pickup_by else "-"]
        if with_book:
            book = app.books.find(item.book_id)
            row.insert(1, book.title if book else item.book_id)
        rows.append(row)
    return rows


def _reserve(app, user, member_id, book):
    reservation, position = app.reservations.reserve(user, member_id, book.book_id)
    success(f"Reserved ({reservation.reservation_id}). You are #{position} in the queue." if member_id == user.user_id
            else f"Reserved ({reservation.reservation_id}) for {member_id}. They are #{position} in the queue.")


def act_on_book(app, user, reference):
    """Borrow or reserve a book chosen from search results."""
    book = app.books.resolve(reference)
    if user.can("borrow_self"):
        member_id = user.user_id
    elif user.can("issue_book"):
        member_id = ask("Member ID").upper()
    else:
        return
    if book.available_copies > 0 or app.reservations.ready_for(member_id, book.book_id):
        if confirm(f"Borrow '{book.title}'?"):
            print_receipt(app.borrowing.issue_book(user, member_id, book.book_id))
    elif confirm(f"'{book.title}' is out. Reserve it?"):
        _reserve(app, user, member_id, book)


# ------------------------------------------------------------------ screens
def search_screen(app, user):
    section("SEARCH BOOKS")
    print("[1] Title  [2] Author  [3] ISBN  [4] Category")
    field = ("title", "author", "isbn", "category")[ask_int("Search by", 1, 4) - 1]
    text = ask("Search text", default="")
    category = ask(f"Category filter ({', '.join(app.books.categories())}; blank = any)", default="")
    available_only = ask("Availability: [1] Any  [2] Available only", default="1") == "2"
    results = app.books.search(field, text, category or None, available_only)
    if not results:
        info("No books found. Try a shorter keyword, a different search type, or remove the filters.")
        return
    table(["ID", "Title", "Author", "Category", "Status"],
          [[b.book_id, b.title, b.author, b.category,
            f"Available ({b.available_copies}/{b.total_copies})" if b.available_copies else "Out"] for b in results])
    if user.can("borrow_self") or user.can("issue_book"):
        reference = ask("Enter book ID to borrow or reserve (blank to go back)", default="")
        if reference:
            act_on_book(app, user, reference)


def inventory_screen(app, user):
    def add():
        book = app.books.add_book(user, ask("ISBN (10 or 13 digits)"), ask("Title"), ask("Author"),
                                  ask("Category", default="General"), ask_int("Total copies", 0, 1000))
        success(f"Book added: {book.book_id} - {book.title}")

    def update():
        book = app.books.resolve(ask("Book ID / ISBN to update"))
        info("Leave a field blank to keep its current value.")
        title = ask(f"Title [{book.title}]", default="")
        author = ask(f"Author [{book.author}]", default="")
        category = ask(f"Category [{book.category}]", default="")
        copies = _optional_int(ask(f"Total copies [{book.total_copies}]", default=""))
        app.books.update_book(user, book.book_id, title=title or None, author=author or None,
                              category=category or None, total_copies=copies)
        success("Book updated.")

    def delete():
        book = app.books.resolve(ask("Book ID / ISBN to delete"))
        if confirm(f"Delete '{book.title}'?"):
            app.books.delete_book(user, book.book_id)
            app.auto_backup("book deleted")
            success("Book deleted.")

    return Menu("BOOK INVENTORY", [
        MenuItem("1", "Add Book", "manage_books", add),
        MenuItem("2", "Update Book", "manage_books", update),
        MenuItem("3", "Delete Book", "manage_books", delete),
        MenuItem("4", "View All", "manage_books", lambda: paginate(
            ["ID", "ISBN", "Title", "Author", "Category", "Avail"],
            [[b.book_id, b.isbn, b.title, b.author, b.category, f"{b.available_copies}/{b.total_copies}"]
             for b in app.books.list_all()], aligns="lllllr")),
        MenuItem("5", "Back", None, lambda: STOP)], user, app.auth).run()


def profile_card(app, member):
    summary = app.reports.member_summary(member.user_id)
    print("\nMEMBER PROFILE")
    for label, value in (("ID", member.user_id), ("Name", member.full_name), ("Username", member.username),
                         ("Status", "Active" if member.active else "Inactive"),
                         ("Contact", member.email or member.phone or "-"),
                         ("Borrowed", f"{summary['borrowed']}/{summary['limit']}"),
                         ("Reservations", summary["reservations"]), ("Fines", money(summary["fines"]))):
        print(f"{label:<13}: {value}")


def register_member(app, user):
    section("REGISTER MEMBER")
    name, username = ask("Full name"), ask("Username")
    contact = ask("Email or phone (optional)", default="")
    email, phone = (contact, "") if "@" in contact else ("", contact)
    password = ask_password("Password")
    if ask_password("Confirm password") != password:
        raise ValidationError("Passwords do not match.")
    member = app.users.add_user(user, username, name, Role.MEMBER, password, email=email, phone=phone)
    success(f"Member registered: {member.user_id} - {member.full_name}")


def member_management_screen(app, user):
    def update():
        member = app.users.get_member(ask("Member ID"))
        info("Leave a field blank to keep its current value.")
        name = ask(f"Full name [{member.full_name}]", default="")
        email = ask(f"Email [{member.email}]", default="")
        phone = ask(f"Phone [{member.phone}]", default="")
        password = ask_password("New password (blank = keep)")
        app.users.update_user(user, member.user_id, full_name=name or None, email=email or None,
                              phone=phone or None, password=password or None)
        success("Member updated.")

    def search():
        found = app.users.search(ask("Search text"), Role.MEMBER)
        table(["ID", "Username", "Name", "Status"],
              [[m.user_id, m.username, m.full_name, "Active" if m.active else "Inactive"] for m in found]) if found \
            else info("No members found.")

    def deactivate():
        member = app.users.get_member(ask("Member ID to deactivate"))
        if confirm(f"Deactivate {member.full_name}?"):
            app.users.deactivate(user, member.user_id)
            success("Member deactivated.")

    return Menu("MEMBER MANAGEMENT", [
        MenuItem("1", "Register Member", "register_member", lambda: register_member(app, user)),
        MenuItem("2", "Update Member", "manage_members", update),
        MenuItem("3", "Search Member", "manage_members", search),
        MenuItem("4", "View Profile", "manage_members", lambda: profile_card(app, app.users.get_member(ask("Member ID")))),
        MenuItem("5", "Deactivate", "manage_members", deactivate),
        MenuItem("6", "Reactivate", "manage_members",
                 lambda: success(f"{app.users.activate(user, ask('Member ID')).user_id} reactivated.")),
        MenuItem("7", "Back", None, lambda: STOP)], user, app.auth).run()


def issue_screen(app, user):
    section("ISSUE BOOK")
    member_id, reference = ask("Member ID").upper(), ask("Book ID / ISBN")
    checks, member, book, _hold = app.borrowing.evaluate_borrow(member_id, reference)
    print(f"\nMember: {member.user_id} - {member.full_name}\nBook  : {book.title} ({book.book_id})\n")
    for label, passed, message, _cls in checks:
        success(label) if passed else error(message)
    failed = [c for c in checks if not c[1]]
    if failed:
        if len(failed) == 1 and failed[0][3] is BookUnavailable and confirm("Reserve this book for the member instead?"):
            _reserve(app, user, member.user_id, book)
        return
    print_receipt(app.borrowing.issue_book(user, member.user_id, book.book_id))


def my_loans_table(app, member_id):
    today = app.today()
    loans = sorted(app.borrowing.loans_for_member(member_id), key=lambda t: t.due_date)
    if not loans:
        info("No books currently borrowed.")
        return loans
    table(["Txn", "Title", "Borrowed", "Due", "Status"],
          [[t.txn_id, app.books.find(t.book_id).title if app.books.find(t.book_id) else t.book_id, t.borrowed_on,
            t.due_date, f"OVERDUE {t.days_overdue(today)} days" if t.is_overdue(today) else f"{t.days_left(today)} days left"]
           for t in loans])
    return loans


def return_screen(app, user):
    section("RETURN BOOK")
    if user.can("return_any"):
        txn_id = ask("Transaction ID (blank = look up by member and book)", default="")
        if not txn_id:
            txn = app.borrowing.find_active(ask("Member ID"), ask("Book ID / ISBN"))
            if txn is None:
                raise NotFoundError("No active loan found for that member and book.")
            txn_id = txn.txn_id
    else:
        if not my_loans_table(app, user.user_id):
            return
        txn_id = ask("Transaction ID to return")
    print_return(app, app.borrowing.return_book(user, txn_id))


def reservations_screen(app, user):
    def queue():
        book = app.books.resolve(ask("Book ID / ISBN"))
        items = app.reservations.all_for_book(book.book_id)
        print(f"\nBook {book.book_id} - {book.title}")
        if not items:
            return info("No reservations for this book.")
        rows = reservation_rows(app, items)
        table(["Reservation", "Member", "Status", "Pickup By"], rows)

    def ready():
        items = app.reservations.ready_reservations()
        table(["Reservation", "Book", "Member", "Status", "Pickup By"], reservation_rows(app, items, True)) \
            if items else info("No ready reservations.")

    def collect():
        print_receipt(app.reservations.mark_collected(user, ask("Reservation ID")))

    def cancel():
        app.reservations.cancel(user, ask("Reservation ID"))
        success("Reservation cancelled.")

    def expiry():
        outcomes = app.reservations.expire_overdue()
        success(f"Expiry check complete. Expired reservations: {len(outcomes)}")
        for expired, promoted in outcomes:
            info(f"  {expired.reservation_id} ({expired.member_id}) expired" +
                 (f" - next in line: {promoted.member_id}, pickup by {promoted.pickup_by}" if promoted else ""))

    def reserve_for_member():
        member_id = ask("Member ID").upper()
        _reserve(app, user, member_id, app.books.resolve(ask("Book ID / ISBN")))

    return Menu("RESERVATIONS", [
        MenuItem("1", "View Queue by Book", "manage_reservations", queue),
        MenuItem("2", "View Ready Reservations", "manage_reservations", ready),
        MenuItem("3", "Mark as Collected (issue held copy)", "manage_reservations", collect),
        MenuItem("4", "Cancel Reservation", "manage_reservations", cancel),
        MenuItem("5", "Run Expiry Check", "manage_reservations", expiry),
        MenuItem("6", "Reserve for a Member", "manage_reservations", reserve_for_member),
        MenuItem("7", "Back", None, lambda: STOP)], user, app.auth).run()


def _fine_rows(fines):
    return [[f.member_id, f.txn_id, f"{f.days_late}d", money(f.amount), money(f.paid), money(f.balance)] for f in fines]


def fines_screen(app, user):
    headers = ["Member", "Txn", "Late", "Amount", "Paid", "Balance"]

    def member_fines():
        member_id = ask("Member ID").upper()
        fines = app.fines.fines_for_member(member_id)
        table(headers, _fine_rows(fines)) if fines else info("This member has no fines.")
        print(f"Outstanding: {money(app.fines.balance_for(member_id))}")

    def pay():
        member_id = ask("Member ID").upper()
        app.users.get_member(member_id)
        info(f"Outstanding balance: {money(app.fines.balance_for(member_id))}")
        receipt = app.fines.record_payment(user, member_id, ask_int("Enter payment amount", 1))
        print(f"\nPAYMENT RECEIPT\nPaid    : {money(receipt['paid'])}\nBalance : {money(receipt['balance'])}")
        success("Payment recorded.")

    def outstanding():
        fines = app.fines.outstanding()
        if fines:
            table(headers, _fine_rows(fines))
        print(f"Total outstanding: {money(app.fines.total_outstanding())}")

    return Menu("FINES", [
        MenuItem("1", "View Member Fines", "manage_fines", member_fines),
        MenuItem("2", "Record Payment", "manage_fines", pay),
        MenuItem("3", "View All Outstanding Fines", "manage_fines", outstanding),
        MenuItem("4", "Back", None, lambda: STOP)], user, app.auth).run()


def show_report(app, user, name):
    report = app.reports.generate(user, name)
    banner(report["title"].upper())
    stat_strip(report["summary"])
    print()
    if "chart" in report:
        bar_chart(report["chart"])
    else:
        paginate(report["headers"], report["rows"])
    for line in report.get("footer", []):
        print(line)


def reports_screen(app, user):
    items = [MenuItem(str(n), title, REPORT_PERMISSIONS[name], lambda name=name: show_report(app, user, name))
             for n, (name, title) in enumerate(REPORT_TITLES.items(), 1)]
    items.append(MenuItem("0", "Back", None, lambda: STOP))
    return Menu("REPORTS & ANALYTICS", items, user, app.auth).run()


# ------------------------------------------------------------------ dashboard
class LibrarianMenu:
    REQUIRED_PERMISSION = "issue_book"

    def __init__(self, app, user):
        self.app, self.user = app, user

    def header(self):
        summary = self.app.reports.desk_summary()
        banner(f"LIBRARIAN DESK - {self.user.full_name}")
        stat_strip([(summary["due_today"], "Due Today"), (summary["overdue"], "Overdue"),
                    (summary["ready"], "Ready Reservations")])

    def run(self):
        app, user = self.app, self.user
        if not app.auth.authorize(user, self.REQUIRED_PERMISSION):
            error("Access denied: this dashboard is for librarians only.")
            return
        Menu("Librarian", [
            MenuItem("1", "Manage Inventory", "manage_books", lambda: inventory_screen(app, user)),
            MenuItem("2", "Register Member", "register_member", lambda: register_member(app, user)),
            MenuItem("3", "Manage Members", "manage_members", lambda: member_management_screen(app, user)),
            MenuItem("4", "Search Books", "search_books", lambda: search_screen(app, user)),
            MenuItem("5", "Issue Book", "issue_book", lambda: issue_screen(app, user)),
            MenuItem("6", "Return Book", "return_any", lambda: return_screen(app, user)),
            MenuItem("7", "Reservations", "manage_reservations", lambda: reservations_screen(app, user)),
            MenuItem("8", "Fines", "manage_fines", lambda: fines_screen(app, user)),
            MenuItem("9", "Overdue Report", "view_ops_reports", lambda: show_report(app, user, "overdue")),
            MenuItem("0", "Logout", None, lambda: STOP)], user, app.auth, header=self.header).run()
