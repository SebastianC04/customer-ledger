import os
import re
import sys
import threading
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import or_, func

import models
import schemas
import backup
from database import engine, get_db, Base, run_migrations

# Create tables on startup if they don't exist yet, then patch any columns
# an older database file is missing (see database.run_migrations).
Base.metadata.create_all(bind=engine)
run_migrations()

app = FastAPI(title="TARJETAS")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ENTRIES_PER_PAGE = 35  # within the 30-40 range requested; change if you want a different page size
BACKUP_CHECK_SECONDS = 60  # how often the scheduler checks for a due backup slot (see backup.py)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, "frozen", False):
    # Running as a bundled PyInstaller exe: files are unpacked into a temp
    # folder at sys._MEIPASS rather than living next to this script.
    FRONTEND_DIR = os.path.join(sys._MEIPASS, "frontend")
else:
    FRONTEND_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "frontend"))


@app.on_event("startup")
def _start_backup_scheduler():
    """Checks every minute whether an hourly backup slot (10:30 AM - 7:00 PM)
    is due; backup.run_scheduled_backup decides and also collapses old days."""
    def _loop():
        while True:
            try:
                backup.run_scheduled_backup()
            except Exception as e:  # never let a backup hiccup kill the thread
                print(f"Scheduled backup failed: {e}", file=sys.stderr)
            time.sleep(BACKUP_CHECK_SECONDS)

    threading.Thread(target=_loop, daemon=True).start()


@app.post("/api/backup")
def trigger_backup():
    """Manual backup trigger, used by the 'Backup Now' button in Manage Backups."""
    path = backup.create_backup()
    if path is None:
        raise HTTPException(status_code=404, detail="No database found to back up yet")
    return {"backup_file": os.path.basename(path)}


@app.get("/api/backups")
def get_backups():
    return backup.list_backups()


@app.post("/api/backups/{filename}/restore")
def restore_backup(filename: str):
    """Overwrites the live database with a chosen backup. A safety backup
    of the current state is taken automatically before doing so."""
    try:
        safety_backup = backup.restore_backup(filename)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Backup not found")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid backup filename")
    # A backup from an older version may lack newer tables/columns; bring the
    # restored file up to date right away instead of waiting for a restart.
    Base.metadata.create_all(bind=engine)
    run_migrations()
    return {"restored_from": filename, "safety_backup": safety_backup}


@app.delete("/api/backups/{filename}")
def delete_backup(filename: str):
    try:
        backup.delete_backup(filename)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Backup not found")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid backup filename")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Balance recalculation
# ---------------------------------------------------------------------------

def recalculate_balances(customer_id: int, db: Session):
    """Recomputes the running total_balance for every entry on a customer's
    card, in order: each entry's balance = previous balance + deposit -
    withdrawal, starting from the customer's opening (carried-over) balance.
    Only called when a NEW entry is added — editing or deleting an existing
    entry (or the opening balance) does not trigger this, so those actions
    never touch other entries' balances (by design)."""
    customer = db.query(models.Customer).filter(models.Customer.id == customer_id).first()
    entries = (
        db.query(models.Entry)
        .filter(models.Entry.customer_id == customer_id)
        .order_by(models.Entry.sort_order, models.Entry.id)
        .all()
    )
    running = (customer.opening_balance or 0.0) if customer else 0.0
    for e in entries:
        # Round each step to cents so float drift (0.1 + 0.2) never builds up.
        running = round(running + (e.deposit or 0.0) - (e.withdrawal or 0.0), 2)
        e.total_balance = running
    db.commit()


# ---------------------------------------------------------------------------
# Customer endpoints
# ---------------------------------------------------------------------------

@app.post("/api/customers", response_model=schemas.CustomerOut)
def create_customer(customer: schemas.CustomerCreate, db: Session = Depends(get_db)):
    """Create a new customer 'card' with name, phone, address, and date created."""
    data = customer.model_dump()
    if not data.get("time_created"):
        # Auto-stamp the current time of day, e.g. "12:23 PM", when the
        # caller didn't supply one.
        formatted = datetime.now().strftime("%I:%M %p")
        data["time_created"] = formatted.lstrip("0") if formatted[0] == "0" else formatted

    db_customer = models.Customer(**data)
    db.add(db_customer)
    db.commit()
    db.refresh(db_customer)
    return db_customer


@app.get("/api/customers", response_model=List[schemas.CustomerOut])
def list_or_search_customers(
    q: Optional[str] = Query(None, description="Search by customer name or phone number"),
    db: Session = Depends(get_db),
):
    """List all customers, or search by name / phone number if q is provided.

    Phone search ignores formatting (searching "510 959 5506", "5109595506", or
    "(510)-959-5506" all match a stored "(510)-959-5506" number).
    """
    query = db.query(models.Customer).order_by(models.Customer.name)
    if not q:
        return query.all()

    like = f"%{q}%"
    q_digits = re.sub(r"\D", "", q)

    if q_digits:
        # Digit-aware phone match: compare digits only, done in Python since
        # SQLite has no built-in regex-strip function to do this in SQL.
        candidates = query.all()
        return [
            c for c in candidates
            if q.lower() in c.name.lower() or q_digits in re.sub(r"\D", "", c.phone_number)
        ]

    return query.filter(models.Customer.name.ilike(like)).all()


@app.get("/api/customers/{customer_id}")
def get_customer(
    customer_id: int,
    page: int = Query(1, ge=1),
    sort: str = Query("added", description="'added' (chronological), 'recent' (newest first), 'deposit', or 'balance'"),
    db: Session = Depends(get_db),
):
    """Get a customer card plus one page of their entries (30-40 entries per page)."""
    customer = db.query(models.Customer).filter(models.Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    total_entries = (
        db.query(func.count(models.Entry.id))
        .filter(models.Entry.customer_id == customer_id)
        .scalar()
    )
    total_pages = max(1, (total_entries + ENTRIES_PER_PAGE - 1) // ENTRIES_PER_PAGE)
    page = min(page, total_pages)

    # Each sort ends with a fixed tie-breaker so equal values never shuffle
    # between pages.
    order_map = {
        "added": (models.Entry.sort_order.asc(), models.Entry.id.asc()),
        "recent": (models.Entry.sort_order.desc(), models.Entry.id.desc()),
        "deposit": (models.Entry.deposit.desc(), models.Entry.sort_order.asc(), models.Entry.id.asc()),
        "balance": (models.Entry.total_balance.desc(), models.Entry.sort_order.asc(), models.Entry.id.asc()),
    }
    order_clause = order_map.get(sort, order_map["added"])

    entries = (
        db.query(models.Entry)
        .filter(models.Entry.customer_id == customer_id)
        .order_by(*order_clause)
        .offset((page - 1) * ENTRIES_PER_PAGE)
        .limit(ENTRIES_PER_PAGE)
        .all()
    )

    return {
        "id": customer.id,
        "name": customer.name,
        "phone_number": customer.phone_number,
        "address": customer.address,
        "date_created": customer.date_created,
        "time_created": customer.time_created,
        "language": customer.language,
        "opening_balance": customer.opening_balance or 0.0,
        "entries": [schemas.EntryOut.model_validate(e) for e in entries],
        "page": page,
        "total_pages": total_pages,
        "total_entries": total_entries,
        "entries_per_page": ENTRIES_PER_PAGE,
        "sort": sort,
    }


@app.put("/api/customers/{customer_id}", response_model=schemas.CustomerOut)
def update_customer(
    customer_id: int, update: schemas.CustomerUpdate, db: Session = Depends(get_db)
):
    """Edit any field on a customer card."""
    customer = db.query(models.Customer).filter(models.Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    for field, value in update.model_dump(exclude_unset=True).items():
        setattr(customer, field, value)
    db.commit()
    db.refresh(customer)
    return customer


@app.delete("/api/customers/{customer_id}")
def delete_customer(customer_id: int, db: Session = Depends(get_db)):
    """Deletes a customer card and every entry on it. Takes a safety backup
    first, so a mistaken delete can be undone from Manage Backups."""
    customer = db.query(models.Customer).filter(models.Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    safety = backup.create_backup(tag=backup.PRE_DELETE_TAG)
    db.delete(customer)
    db.commit()
    return {"ok": True, "backup": os.path.basename(safety) if safety else None}


# ---------------------------------------------------------------------------
# Entry endpoints
# ---------------------------------------------------------------------------

@app.post("/api/customers/{customer_id}/entries", response_model=schemas.EntryOut)
def add_entry(customer_id: int, entry: schemas.EntryCreate, db: Session = Depends(get_db)):
    """Add a new item (date, description, deposit, withdrawal) to a customer's
    card. Total balance is computed automatically: it isn't accepted from
    the caller. The time of day is auto-stamped the same way — not
    accepted from the caller, always "now" at the moment the item is added."""
    customer = db.query(models.Customer).filter(models.Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    # One add at a time: two requests arriving together (a double-click)
    # would otherwise both read the same max sort_order and collide.
    with _add_entry_lock:
        max_order = (
            db.query(func.max(models.Entry.sort_order))
            .filter(models.Entry.customer_id == customer_id)
            .scalar()
        )
        next_order = (max_order or 0) + 1

        formatted_time = datetime.now().strftime("%I:%M %p")
        formatted_time = formatted_time.lstrip("0") if formatted_time[0] == "0" else formatted_time

        db_entry = models.Entry(
            customer_id=customer_id, sort_order=next_order, time=formatted_time,
            **entry.model_dump()
        )
        db.add(db_entry)
        db.commit()
        recalculate_balances(customer_id, db)
    db.refresh(db_entry)
    return db_entry


_add_entry_lock = threading.Lock()


@app.put("/api/entries/{entry_id}", response_model=schemas.EntryOut)
def update_entry(entry_id: int, update: schemas.EntryUpdate, db: Session = Depends(get_db)):
    """Edit any field on an existing entry/item. Per your call: editing an
    entry does NOT recalculate any total_balance, on this row or any other
    — automatic balance calculation only happens when a NEW item is added
    (see add_entry). Editing deposit/withdrawal here changes those figures
    only; total_balance is untouched."""
    entry = db.query(models.Entry).filter(models.Entry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    for field, value in update.model_dump(exclude_unset=True).items():
        setattr(entry, field, value)
    db.commit()
    db.refresh(entry)
    return entry


@app.delete("/api/entries/{entry_id}")
def delete_entry(entry_id: int, db: Session = Depends(get_db)):
    """Deletes an entry. No recalculation of other entries' total_balance
    happens here either, for the same reason as update_entry above."""
    entry = db.query(models.Entry).filter(models.Entry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    db.delete(entry)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# History / activity endpoint
# ---------------------------------------------------------------------------

@app.get("/api/item-descriptions")
def list_item_descriptions(db: Session = Depends(get_db)):
    """Every distinct item description used so far, for the History item filter."""
    rows = db.query(models.Entry.item_description).distinct().all()
    seen = {}
    for (desc,) in rows:
        if desc and desc.strip():
            seen.setdefault(desc.strip().casefold(), desc.strip())
    return sorted(seen.values(), key=str.casefold)


def _fold(s: str) -> str:
    """Lowercase without accents, so 'camison' matches 'Camisón'."""
    return "".join(
        ch for ch in unicodedata.normalize("NFD", s) if not unicodedata.combining(ch)
    ).casefold()


def _parse_time_or_none(entry_time: Optional[str]):
    if not entry_time:
        return None
    try:
        return schemas.parse_time(entry_time)
    except ValueError:
        return None


@app.get("/api/history")
def get_history(
    start: str = Query(..., description="Start of range, yyyy-mm-dd or mm/dd/yyyy"),
    end: str = Query(..., description="End of range, yyyy-mm-dd or mm/dd/yyyy"),
    customer_ids: Optional[str] = Query(None, description="Comma-separated customer ids; omit for the whole database"),
    item: Optional[str] = Query(None, description="Item description contains this text (case-insensitive)"),
    start_time: Optional[str] = Query(None, description="Earliest time of day, e.g. 10:00 or 10:00 AM"),
    end_time: Optional[str] = Query(None, description="Latest time of day, e.g. 19:00 or 7:00 PM"),
    db: Session = Depends(get_db),
):
    """Ledger entries matching every given filter (date range, customers,
    item description, time of day), plus totals for the whole match and
    per item description. Used by the History panel."""
    try:
        start_dt = schemas.parse_flexible_date(start)
        end_dt = schemas.parse_flexible_date(end)
        t_from = schemas.parse_time(start_time) if start_time else None
        t_to = schemas.parse_time(end_time) if end_time else None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    ids = None
    if customer_ids is not None:
        try:
            ids = [int(x) for x in customer_ids.split(",") if x.strip()]
        except ValueError:
            raise HTTPException(status_code=400, detail="customer_ids must be numbers")

    item_q = _fold(item.strip()) if item and item.strip() else None

    def time_ok(t) -> bool:
        if t_from is None and t_to is None:
            return True
        if t is None:
            return False  # time filter set but entry has no time recorded
        if t_from and t_to and t_from > t_to:  # overnight range, e.g. 8 PM - 2 AM
            return t >= t_from or t <= t_to
        return (t_from is None or t >= t_from) and (t_to is None or t <= t_to)

    query = db.query(models.Entry, models.Customer).join(
        models.Customer, models.Entry.customer_id == models.Customer.id
    )
    if ids is not None:
        query = query.filter(models.Entry.customer_id.in_(ids))

    results = []
    by_item: dict[str, dict] = {}
    total_deposit = 0.0
    total_withdrawal = 0.0
    for entry, customer in query.all():
        if not entry.date:
            continue
        try:
            entry_dt = schemas.parse_flexible_date(entry.date)
        except ValueError:
            continue
        if not (start_dt <= entry_dt <= end_dt):
            continue
        desc = (entry.item_description or "").strip()
        if item_q and item_q not in _fold(desc):
            continue
        entry_t = _parse_time_or_none(entry.time)
        if not time_ok(entry_t):
            continue

        dep = entry.deposit or 0.0
        wd = entry.withdrawal or 0.0
        total_deposit += dep
        total_withdrawal += wd

        group = by_item.setdefault(desc.casefold(), {
            "item_description": desc, "total_deposit": 0.0,
            "total_withdrawal": 0.0, "count": 0,
        })
        group["total_deposit"] += dep
        group["total_withdrawal"] += wd
        group["count"] += 1

        results.append({
            "entry_id": entry.id,
            "date": entry.date,
            "time": entry.time,
            "item_description": entry.item_description,
            "deposit": entry.deposit,
            "withdrawal": entry.withdrawal,
            "total_balance": entry.total_balance,
            "customer_id": customer.id,
            "customer_name": customer.name,
            "customer_phone": customer.phone_number,
            "_sort": (entry_dt, entry_t or datetime.min.time()),
        })

    results.sort(key=lambda r: r["_sort"], reverse=True)  # newest date, then latest time, first
    for r in results:
        del r["_sort"]

    items = sorted(by_item.values(), key=lambda g: g["item_description"].casefold())
    for g in items:
        g["total_deposit"] = round(g["total_deposit"], 2)
        g["total_withdrawal"] = round(g["total_withdrawal"], 2)
        g["net"] = round(g["total_deposit"] - g["total_withdrawal"], 2)

    return {
        "start": start_dt.strftime("%m/%d/%Y"),
        "end": end_dt.strftime("%m/%d/%Y"),
        "count": len(results),
        "total_deposit": round(total_deposit, 2),
        "total_withdrawal": round(total_withdrawal, 2),
        "net": round(total_deposit - total_withdrawal, 2),
        "entries": results,
        "by_item": items,
    }


# ---------------------------------------------------------------------------
# One-time repair: dates shifted by the old "after 5 PM" bug
# ---------------------------------------------------------------------------
# Older versions filled in "today" using UTC, so anything added after 5 PM
# Pacific (4 PM in winter) got the next day's date. Entries can be detected
# exactly: created_at is stored in UTC, so a buggy entry's date equals
# created_at's UTC date AND is one day after its local date. Entries made by
# the fixed version are saved with the local date, so they never match.
#
# Cards made by older versions have no created_at, so a card is only a
# candidate when its time_created was late enough that UTC was already the
# next day. It's *confirmed* when the card's first item was added before the
# card's stored date and time (impossible unless that date is a day ahead).
# Cards made by the fixed version have created_at and are never candidates;
# fixed cards get date_fix_applied so they can't be shifted twice.

def _utc_naive_to_local_naive(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)


def _shifted_entries(db: Session):
    """(entry, customer, correct_local_date) for every entry that matches the bug."""
    found = []
    rows = db.query(models.Entry, models.Customer).join(
        models.Customer, models.Entry.customer_id == models.Customer.id
    )
    for entry, customer in rows.all():
        if not entry.created_at or not entry.date:
            continue
        try:
            stored = schemas.parse_flexible_date(entry.date).date()
        except ValueError:
            continue
        created_utc = entry.created_at
        if created_utc.tzinfo is not None:
            created_utc = created_utc.astimezone(timezone.utc).replace(tzinfo=None)
        local_day = created_utc.replace(tzinfo=timezone.utc).astimezone().date()
        if stored == created_utc.date() and stored == local_day + timedelta(days=1):
            found.append((entry, customer, local_day))
    return found


def _possibly_shifted_customers(db: Session):
    """(customer, possible_correct_date, confirmed) for old-version cards
    created late enough in the day that the bug would have stamped tomorrow's
    date. confirmed=True when the card's first item predates the card's
    stored date/time; otherwise it can't be told apart from a correct card."""
    first_item = dict(
        db.query(models.Entry.customer_id, func.min(models.Entry.created_at))
        .group_by(models.Entry.customer_id)
        .all()
    )
    found = []
    for c in db.query(models.Customer).all():
        if (not c.date_created or not c.time_created
                or c.date_fix_applied or c.created_at is not None):
            continue
        try:
            stored = schemas.parse_flexible_date(c.date_created).date()
            t = schemas.parse_time(c.time_created)
        except ValueError:
            continue
        candidate = stored - timedelta(days=1)
        # Naive datetime -> astimezone() treats it as this computer's local time.
        utc_day = datetime.combine(candidate, t).astimezone().astimezone(timezone.utc).date()
        if utc_day != stored:
            continue
        first = first_item.get(c.id)
        if isinstance(first, str):  # some SQLite drivers hand back text for MIN()
            first = datetime.fromisoformat(first)
        confirmed = bool(first) and _utc_naive_to_local_naive(first) < datetime.combine(stored, t)
        found.append((c, candidate, confirmed))
    return found


@app.get("/api/maintenance/shifted-dates")
def list_shifted_dates(db: Session = Depends(get_db)):
    fmt = lambda d: d.strftime("%m/%d/%Y")
    return {
        "entries": [{
            "entry_id": e.id, "customer_id": c.id, "customer_name": c.name,
            "item_description": e.item_description, "time": e.time,
            "current_date": e.date, "correct_date": fmt(day),
        } for e, c, day in _shifted_entries(db)],
        "customers": [{
            "customer_id": c.id, "customer_name": c.name, "time_created": c.time_created,
            "current_date": c.date_created, "possible_date": fmt(day),
            "confirmed": confirmed,
        } for c, day, confirmed in _possibly_shifted_customers(db)],
    }


@app.post("/api/maintenance/fix-dates")
def fix_shifted_dates(req: schemas.DateFixRequest, db: Session = Depends(get_db)):
    """Moves the chosen entries/cards back one day. Takes a safety backup
    first. Only items that still match the bug are changed, so a stale or
    repeated request can't shift anything twice."""
    entry_ids = set(req.entry_ids)
    customer_ids = set(req.customer_ids)
    entries = [(e, day) for e, _, day in _shifted_entries(db) if e.id in entry_ids]
    customers = [(c, day) for c, day, _ in _possibly_shifted_customers(db) if c.id in customer_ids]
    if not entries and not customers:
        return {"entries_fixed": 0, "customers_fixed": 0, "backup": None}

    safety = backup.create_backup(tag=backup.PRE_DATE_FIX_TAG)
    for e, day in entries:
        e.date = day.strftime("%m/%d/%Y")
    for c, day in customers:
        c.date_created = day.strftime("%m/%d/%Y")
        c.date_fix_applied = 1
    db.commit()
    return {
        "entries_fixed": len(entries),
        "customers_fixed": len(customers),
        "backup": os.path.basename(safety) if safety else None,
    }


# ---------------------------------------------------------------------------
# Frontend (serve the single-page app)
# ---------------------------------------------------------------------------

@app.get("/")
def serve_index():
    # no-cache: always revalidate, so an updated app never shows a stale UI
    return FileResponse(
        os.path.join(FRONTEND_DIR, "index.html"),
        headers={"Cache-Control": "no-cache"},
    )


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")