"""Book inventory management."""
from config import NotFoundError, ValidationError, audited, check_permission, next_number
from models import Book, normalize_isbn
from storage import PersistentCollection, UnitOfWork

SEARCH_FIELDS = ("title", "author", "isbn", "category")


class BookService(PersistentCollection):
    FILE = "books"

    def __init__(self, storage, activity):
        super().__init__(storage)
        self.activity = activity
        self._by_id = {}
        self._by_isbn = {}  # ISBN -> book index for O(1) duplicate checks
        self.borrowing = self.reservations = None
        self.reload()

    def attach(self, borrowing, reservations):
        self.borrowing, self.reservations = borrowing, reservations

    def _serialize(self):
        return [b.to_dict() for b in self._by_id.values()]

    def _load(self, raw):
        self._by_id = {}
        for row in raw:
            book = Book.from_dict(row)
            self._by_id[book.book_id] = book
        self._by_isbn = {b.isbn: b for b in self._by_id.values()}

    # -- queries
    def find(self, book_id):
        return self._by_id.get(str(book_id).strip().upper())

    def get(self, book_id):
        book = self.find(book_id)
        if book is None:
            raise NotFoundError(f"Book '{book_id}' was not found.")
        return book

    def resolve(self, reference):
        """Find a book by its ID or its ISBN."""
        book = self.find(reference)
        if book:
            return book
        try:
            book = self._by_isbn.get(normalize_isbn(reference))
        except ValidationError:
            book = None
        if book is None:
            raise NotFoundError(f"No book found for '{reference}'.")
        return book

    def list_all(self):
        return sorted(self._by_id.values(), key=lambda b: b.book_id)

    def categories(self):
        return sorted({b.category for b in self._by_id.values()})

    def search(self, field="title", text="", category=None, available_only=False):
        if field not in SEARCH_FIELDS:
            raise ValidationError(f"Cannot search by '{field}'.")
        text = text.strip().lower()
        if field == "isbn":
            text = text.replace("-", "").replace(" ", "")
        wanted = (category or "").strip().lower()
        results = [b for b in self._by_id.values()
                   if (not text or text in getattr(b, field).lower())
                   and (not wanted or b.category.lower() == wanted)
                   and (not available_only or b.available_copies > 0)]
        return sorted(results, key=lambda b: b.book_id)

    # -- commands
    @audited("BOOK_ADD", lambda b: f"{b.book_id} {b.title}")
    def add_book(self, actor, isbn, title, author, category, total_copies):
        check_permission(actor, "manage_books", self.activity)
        isbn = normalize_isbn(isbn)
        if isbn in self._by_isbn:
            raise ValidationError(f"Duplicate ISBN: {isbn} already belongs to '{self._by_isbn[isbn].title}'.")
        book = Book(f"B{next_number(self._by_id):03d}", isbn, title, author, category, total_copies)
        with UnitOfWork(self):
            self._by_id[book.book_id] = book
            self._by_isbn[book.isbn] = book
        return book

    @audited("BOOK_UPDATE", lambda b: f"{b.book_id} {b.title}")
    def update_book(self, actor, book_id, isbn=None, title=None, author=None, category=None, total_copies=None):
        check_permission(actor, "manage_books", self.activity)
        book = self.get(book_id)
        with UnitOfWork(self, self.reservations):
            if isbn is not None:
                new_isbn = normalize_isbn(isbn)
                if new_isbn != book.isbn and new_isbn in self._by_isbn:
                    raise ValidationError(f"Duplicate ISBN: {new_isbn} already exists.")
                self._by_isbn.pop(book.isbn, None)
                book.isbn = new_isbn
                self._by_isbn[new_isbn] = book
            for attr, value in (("title", title), ("author", author), ("category", category)):
                if value is not None:
                    if attr != "category" and not value.strip():
                        raise ValidationError(f"{attr.title()} cannot be empty.")
                    setattr(book, attr, value.strip() or "General")
            if total_copies is not None:
                grew = total_copies > book.total_copies
                book.set_total_copies(total_copies)
                if grew:
                    self.reservations.fill_available(book)  # new copies go to waiting members first
        return book

    @audited("BOOK_DELETE", lambda b: f"{b.book_id} {b.title}")
    def delete_book(self, actor, book_id):
        check_permission(actor, "manage_books", self.activity)
        book = self.get(book_id)
        on_loan = self.borrowing.active_count_for_book(book.book_id)
        if on_loan:
            raise ValidationError(f"Cannot delete: {on_loan} cop{'y' if on_loan == 1 else 'ies'} currently borrowed")
        if self.reservations.active_for_book(book.book_id):
            raise ValidationError("Cannot delete: book has active reservations")
        with UnitOfWork(self):
            del self._by_id[book.book_id]
            self._by_isbn.pop(book.isbn, None)
        return book
