"""Smart Library & Resource Management System - entry point.  Run:  python main.py"""
import sys

from cli.auth_menu import AuthMenu, show_startup
from cli.display import error, info
from config import LibraryError
from services import ServiceContainer


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    app = None
    try:
        app = ServiceContainer()
        summary = app.startup()
        show_startup(app, summary)
        AuthMenu(app).run()
    except (KeyboardInterrupt, EOFError):
        print()
    except LibraryError as exc:
        error(f"Fatal error: {exc}")
        return 1
    finally:
        if app is not None:
            app.shutdown()
    info("Data saved. Goodbye!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
