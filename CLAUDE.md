# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A local, single-machine replacement for a paper customer ledger, in daily use at a family check-cashing business. FastAPI + SQLAlchemy + SQLite backend; the whole UI is one vanilla-JS file (`frontend/index.html`) with no build step. Packaged for Windows as a pywebview desktop app. Nothing leaves the machine.

## Commands

All backend commands run from `backend/` (modules import each other as top-level names, e.g. `from database import ...`, so the working directory matters).

```
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload        # http://127.0.0.1:8000, serves the frontend too
python launcher.py               # run as the desktop app (native pywebview window)
```

Seed sample data (app must be running; run from repo root): `python seed_customers.py` — POSTs `customers_seed.json` to the API.

Bigger demo data set: `python seed_demo_data.py` (repo root) writes ~120 invented customers and ~2,000 entries straight into `backend/ledger.db`, with realistic times of day and 6 entries carrying the old after-5 PM date mistake. To start over, stop the app and delete `backend/ledger.db`.

Search (customer list, History customer picker, History item filter) ignores case and accents.

On this Windows machine `uvicorn --reload` hangs on reload (old process keeps serving), so restart the server manually after backend changes. `index.html` is served with `Cache-Control: no-cache`.

There is no test suite, linter, or formatter configured. Packaging scripts (`build_installer/`, PyInstaller + Inno Setup) are not in this repo; see `USB_INSTALL_GUIDE.md`.

## Architecture

- **`main.py`** holds the FastAPI app and every endpoint (`/api/customers`, `/api/entries`, `/api/history`, `/api/backup(s)`), plus serving `frontend/` at `/` and `/static`. On import it runs `Base.metadata.create_all` and `run_migrations()`; on startup it starts the backup scheduler thread (see `backup.py` below).
- **`database.py`** decides where data lives: next to the code when run from source, `%LOCALAPPDATA%\CustomerLedger\` when frozen by PyInstaller (`sys.frozen`). Backups go in a `backups/` folder beside `ledger.db`.
- **Schema changes:** there is no Alembic. `create_all` only creates missing tables, so any new column on an existing table **must** also be added to `expected_columns` in `database.run_migrations()` or older user databases will crash.
- **`schemas.py`** (Pydantic v2) normalizes and validates input: phones → `(xxx)-xxx-xxxx`, dates → `mm/dd/yyyy`, times → `h:mm AM/PM`. Dates/times are stored as these **strings**, not date types, so date-range filtering (history) parses them via `parse_flexible_date`.
- **`backup.py`** uses SQLite's online backup API (not file copies) and validates filenames against path traversal. A thread in `main.py` calls `run_scheduled_backup()` every minute: backups happen once per hourly slot from 10:30 AM to 7:00 PM, only if the DB changed. Each finished day collapses to its last backup unless it holds a `_prerestore`, `_predatefix` or `_predelete` safety backup (deleting a customer takes a `_predelete` one); days older than 30 are deleted.
- **History** (`/api/history`): all filters combine — date range, `customer_ids` (comma list), `item` (case-insensitive, accent-insensitive substring), `start_time`/`end_time`. Returns every match plus overall totals and `by_item` groups. Filtering is done in Python because dates/times are stored as text; sorting and paging (20/30/50/100/All) happen in the browser.
- **Starting balance** (`Customer.opening_balance`) shows on the card as a display-only "Balance carried over" row. It is not an entry or a deposit, so it never counts in History totals.
- **Dates in the frontend:** use `localIsoDate()`, never `toISOString()` (UTC rolls over to tomorrow at 5 PM Pacific).
- **Date-fix tool** (`/api/maintenance/shifted-dates`, `/api/maintenance/fix-dates`): repairs data saved by older versions that had that UTC bug. An entry is wrong when its date equals `created_at`'s UTC date (created_at is stored in UTC) and is one day after its local date. Cards made by older versions have no `Customer.created_at`; a late-created one is "confirmed" when its first entry predates the card's stored date/time, otherwise "can't tell". Cards made now get `created_at` and are never flagged; `date_fix_applied` stops a card being shifted twice. Note the `created_at` column default fires on insert even when given None. A `_predatefix` backup is taken first. The UI shows a notice on the customer list and a review window (also under Manage Backups).
- **`launcher.py`** is the PyInstaller entry point: runs uvicorn on a thread at 127.0.0.1:8000 and opens a pywebview window.
- **Frontend:** `frontend/index.html` contains all markup, CSS, and JS. UI strings live in the `TRANSLATIONS` object (English and Spanish) accessed via `t(key)`; add both languages when adding strings. Customers can override the app language per card (`Customer.language`).

## Balance semantics (intentional — don't "fix")

- `total_balance` is server-computed and never accepted from the client. Entry `time` is auto-stamped on creation and not client-settable.
- `recalculate_balances()` runs **only when a new entry is added** and recomputes every entry on that customer's card in `sort_order`, starting from `Customer.opening_balance` (balance carried over from the old paper system). Editing or deleting an entry deliberately does not touch any balance (the owner's explicit choice), so balances can look stale until the next entry is added.
- Entries are ordered by `sort_order` (max + 1 per customer), not by date. Card pagination is computed on the fly with `ENTRIES_PER_PAGE` (35) in `main.py`.
