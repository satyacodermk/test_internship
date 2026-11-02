"""Book model with validated copy counts."""
from dataclasses import asdict, dataclass

from config import ValidationError


def normalize_isbn(isbn):
    """Strip separators and validate the ISBN-10 / ISBN-13 format."""
    value = str(isbn).replace("-", "").replace(" ", "").upper()
    ok13 = len(value) == 13 and value.isdigit()
    ok10 = len(value) == 10 and value[:9].isdigit() and (value[9].isdigit() or value[9] == "X")
    if not (ok13 or ok10):
        raise ValidationError("Invalid ISBN format (use 10 or 13 digits).")
    return value


@dataclass
class Book:
    book_id: str
    isbn: str
    title: str
    author: str
    category: str
    total_copies: int
    available_copies: int | None = None

    def __post_init__(self):
        self.isbn = normalize_isbn(self.isbn)
        self.title = str(self.title).strip()
        self.author = str(self.author).strip()
        self.category = str(self.category).strip() or "General"
        if not self.title or not self.author:
            raise ValidationError("Title and author are required.")
        if not isinstance(self.total_copies, int) or isinstance(self.total_copies, bool) or self.total_copies < 0:
            raise ValidationError("Total copies must be a whole number of 0 or more.")
        if self.available_copies is None:
            self.available_copies = self.total_copies
        if not 0 <= self.available_copies <= self.total_copies:
            raise ValidationError("Available copies must be between 0 and total copies.")

    @property
    def is_available(self):
        return self.available_copies > 0

    @property
    def copies_out(self):
        """Copies on loan or held for a reservation."""
        return self.total_copies - self.available_copies

    def checkout(self):
        if self.available_copies <= 0:
            raise ValidationError(f"No copies of '{self.title}' are available.")
        self.available_copies -= 1

    def check_in(self):
        if self.available_copies >= self.total_copies:
            raise ValidationError(f"Data error: all copies of '{self.title}' are already on the shelf.")
        self.available_copies += 1

    def set_total_copies(self, new_total):
        if not isinstance(new_total, int) or isinstance(new_total, bool) or new_total < 0:
            raise ValidationError("Total copies must be a whole number of 0 or more.")
        new_available = self.available_copies + (new_total - self.total_copies)
        if new_available < 0:
            raise ValidationError(
                f"Cannot reduce total copies to {new_total}: {self.copies_out} copies are on loan or on hold.")
        self.total_copies, self.available_copies = new_total, new_available

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(data["book_id"], data["isbn"], data["title"], data.get("author", "Unknown"),
                   data.get("category", "General"), data["total_copies"], data["available_copies"])
