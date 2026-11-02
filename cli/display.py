"""Console helpers: colours, banners, tables, prompts, charts and the dynamic permission-aware Menu."""
import getpass
import os
import sys
from dataclasses import dataclass
from typing import Callable

from config import CURRENCY, PAGE_SIZE, LibraryError

STOP = object()  # a handler returns this to leave its menu
NAV = object()   # returned by menus themselves so the parent does not pause

_COLORS = {"red": "31", "green": "32", "yellow": "33", "blue": "34", "magenta": "35", "cyan": "36",
           "bold": "1", "dim": "2"}
_RED = {"OVERDUE", "FAIL", "OUT", "EXPIRED", "INACTIVE", "CANCELLED", "ACCESS_DENIED", "LOGIN_FAILED"}
_GREEN = {"OK", "AVAILABLE", "ACTIVE", "READY", "RETURNED", "COLLECTED", "VALID"}
_YELLOW = {"WAITING"}


def paint(text, color):
    if color not in _COLORS or not sys.stdout.isatty() or "NO_COLOR" in os.environ:
        return str(text)
    return f"\033[{_COLORS[color]}m{text}\033[0m"


def _tint(cell):
    words = cell.split()
    word = words[0].upper() if words else ""
    if word in _RED:
        return paint(cell, "red")
    if word in _GREEN:
        return paint(cell, "green")
    if word in _YELLOW:
        return paint(cell, "yellow")
    return cell


# ---------------------------------------------------------------- output
def money(amount):
    return f"{CURRENCY} {amount}"


def banner(title, width=40):
    width = max(width, len(title) + 6)
    print("\n" + paint("=" * width, "cyan"))
    print(paint(title.center(width), "bold"))
    print(paint("=" * width, "cyan"))


def section(title):
    print("\n" + paint(f"--- {title} ---", "cyan"))


def success(message):
    print(paint(f"✔ {message}", "green"))


def error(message):
    print(paint(f"✖ {message}", "red"))


def warn(message):
    print(paint(f"⚠ {message}", "yellow"))


def info(message):
    print(message)


def table(headers, rows, aligns=None):
    """Print an aligned table. `aligns` is a string such as 'llr' (left / right per column)."""
    rows = [[str(c) for c in row] for row in rows]
    headers = [str(h) for h in headers]
    aligns = aligns or "l" * len(headers)
    widths = [max([len(h)] + [len(r[i]) for r in rows]) for i, h in enumerate(headers)]

    def fmt(cells, tint=False):
        parts = []
        for cell, width, align in zip(cells, widths, aligns):
            padded = cell.rjust(width) if align == "r" else cell.ljust(width)
            parts.append(_tint(padded) if tint else padded)
        return "  ".join(parts)

    print(paint(fmt(headers), "bold"))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(fmt(row, tint=True))


def paginate(headers, rows, page_size=PAGE_SIZE, aligns=None):
    if not rows:
        info("Nothing to show.")
        return
    pages, page = (len(rows) - 1) // page_size + 1, 0
    while True:
        table(headers, rows[page * page_size:(page + 1) * page_size], aligns)
        if pages == 1:
            return
        print(f"Page {page + 1}/{pages}")
        command = input("N = next page, P = previous, B = back: ").strip().lower()
        if command == "n" and page < pages - 1:
            page += 1
        elif command == "p" and page > 0:
            page -= 1
        elif command == "b":
            return


def stat_strip(items):
    """Print a row of big numbers with labels underneath."""
    cells = [(str(value), label) for value, label in items]
    widths = [max(len(v), len(l)) + 3 for v, l in cells]
    print(paint("".join(v.center(w) for (v, _), w in zip(cells, widths)), "bold"))
    print("".join(l.center(w) for (_, l), w in zip(cells, widths)))


def bar_chart(rows, width=30):
    peak = max((value for _, value in rows), default=0)
    label_width = max((len(label) for label, _ in rows), default=0)
    for label, value in rows:
        length = int(round(value / peak * width)) if peak else 0
        length = max(length, 1) if value else 0
        print(f"{label.ljust(label_width)} | {('#' * length).ljust(width)} {value}")


# ---------------------------------------------------------------- input
def ask(prompt, default=None):
    """Ask for text. default=None means required; default='' allows a blank answer."""
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{prompt}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return str(default)
        error("This field is required.")


def ask_int(prompt, minimum=None, maximum=None, default=None):
    while True:
        raw = ask(prompt, None if default is None else default)
        try:
            value = int(raw)
        except ValueError:
            error("Please enter a whole number.")
            continue
        if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
            error(f"Enter a number between {minimum} and {maximum}.")
            continue
        return value


# def ask_password(prompt="Password"):
#     return getpass.getpass(f"{prompt}: ")


def ask_password(prompt="Password"):
    if sys.stdin.isatty():                 # real person at a keyboard -> hidden typing
        return getpass.getpass(f"{prompt}: ")
    return input(f"{prompt}: ")            # piped input (tests, scripts, CI) -> read from stdin


def confirm(prompt):
    return input(f"{prompt} (y/n): ").strip().lower() in ("y", "yes")


def pause(message="Press Enter to continue"):
    input(f"\n{message}...")


# ---------------------------------------------------------------- dynamic menus
@dataclass
class MenuItem:
    key: str
    label: str
    permission: str | None
    handler: Callable


class Menu:
    """A numbered menu that only shows options the user's role is allowed to use."""

    def __init__(self, title, items, user, auth, header=None):
        self.title, self.items, self.user, self.auth, self.header = title, items, user, auth, header

    def _allowed(self, item):
        return item.permission is None or self.auth.has(self.user, item.permission)

    def run(self):
        while True:
            if self.header:
                self.header()
            else:
                section(self.title)
            visible = [i for i in self.items if self._allowed(i)]
            print()
            for item in visible:
                print(f"  {item.key}. {item.label}")
            choice = ask("\nChoose an option")
            item = next((i for i in visible if i.key == choice), None)
            if item is None:
                hidden = next((i for i in self.items if i.key == choice), None)
                if hidden is not None:
                    self.auth.authorize(self.user, hidden.permission)
                    error("Access denied: your role cannot use this option.")
                else:
                    error("Invalid choice. Please pick a number from the menu.")
                continue
            try:
                result = item.handler()
            except LibraryError as exc:
                error(str(exc))
                continue
            if result is STOP:
                return NAV
            if result is not NAV:
                pause()
