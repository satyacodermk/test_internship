# Smart Library & Resource Management System

A command-line library system in pure Python 3.10+ (standard library only). Three roles (Admin, Librarian, Member)
see menus made for them; all data is stored in JSON files with atomic saves, automatic backups and crash recovery.

## Run
```
python main.py                              # start the app
python -m unittest discover -s tests -t .   # run all unit tests (use temp folders, real data untouched)
```

## Demo accounts (created on first start)
| Role      | Username | Password  |
|-----------|----------|-----------|
| Admin     | admin    | admin123  |
| Librarian | lib1     | lib123    |
| Members   | riya, amit, sneha, rohan, neha | member123 |

40 sample books are loaded automatically. Change the passwords after the first login (Admin > Manage Users > Update).

## Architecture
```
main.py            entry point (startup screen -> login -> role dashboard)
config.py          paths, roles + permission map (RBAC), settings, errors, activity log
models/            User, Member(User), Book, Transaction, Reservation, Fine
services/          business rules (validate first, then change) + ServiceContainer wiring
storage/           json_storage (atomic writes, validation, recovery, UnitOfWork) + backup_service
cli/               display helpers, dynamic permission-aware Menu, auth/admin/librarian/member screens
data/ logs/ backups/ tests/
```

Key design points
- **RBAC**: `config.PERMISSIONS` maps each role to allowed operations; services check permission before every action,
  menus hide forbidden options, denied attempts are logged as `ACCESS_DENIED`.
- **Passwords**: salted PBKDF2-HMAC-SHA256, compared with `hmac.compare_digest`; accounts lock after repeated failures.
- **Transaction consistency**: `UnitOfWork` snapshots affected collections, applies all changes, saves them together and
  rolls back memory + files if anything fails.
- **Reservations**: FIFO queue (`collections.deque`). A returned copy is *held* (not shelved) for the first waiting member
  with a pickup deadline; uncollected holds expire and pass to the next person.
- **Fines**: only days after the due date count; fines above the limit (recorded + accruing on overdue loans) block borrowing.
- **Recovery**: at start-up every file is validated; broken/missing files are restored from the newest valid backup or recreated.
- **Efficiency**: ISBN and ID index dictionaries; the activity log is streamed line by line; services take injected
  storage/clock so tests never touch real files.

## Settings (Admin > Library Settings)
Borrow limit (3), duration (14 days), fine rate (Rs 5/day), fine limit (Rs 100), pickup window (2 days),
extension days (7), max extensions (1), login attempts (3), lockout minutes (5). Existing loans keep their due dates.
