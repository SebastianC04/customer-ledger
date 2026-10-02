import os
import re
import sys
import threading
import time
from datetime import datetime
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
BACKUP_INTERVAL_SECONDS = 30 * 60  # automatic background backup cadence while the app is open

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, "frozen", False):
    # Running as a bundled PyInstaller exe: files are unpacked into a temp
    # folder at sys._MEIPASS rather than living next to this script.
    FRONTEND_DIR = os.path.join(sys._MEIPASS, "frontend")
else:
    FRONTEND_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "frontend"))


@app.on_event("startup")
def _startup_backup_and_scheduler():
    # One backup right away so every session starts from a known-good copy...
    backup.create_backup()

    # ...then keep backing up periodically for as long as the app stays open.
    def _loop():
        while True:
            time.sleep(BACKUP_INTERVAL_SECONDS)
            backup.create_backup()

    threading.Thread(target=_loop, daemon=True).start()


@app.post("/api/backup")
def trigger_backup():
    """Manual backup trigger, used by the 'Backup Now' button."""
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
    withdrawal, starting from 0. Only called when a NEW entry is added —
    editing or deleting an existing entry does not trigger this, so those
    actions never touch other entries' balances (by design)."""
    entries = (
        db.query(models.Entry)
        .filter(models.Entry.customer_id == customer_id)
        .order_by(models.Entry.sort_order)
        .all()
    )
    running = 0.0
    for e in entries:
        running += (e.deposit or 0.0) - (e.withdrawal or 0.0)
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

    order_map = {
        "added": models.Entry.sort_order.asc(),
        "recent": models.Entry.sort_order.desc(),
        "deposit": models.Entry.deposit.desc(),
        "balance": models.Entry.total_balance.desc(),
    }
    order_clause = order_map.get(sort, models.Entry.sort_order.asc())

    entries = (
        db.query(models.Entry)
        .filter(models.Entry.customer_id == customer_id)
        .order_by(order_clause)
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
    customer = db.query(models.Customer).filter(models.Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    db.delete(customer)
    db.commit()
    return {"ok": True}


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

@app.get("/api/history")
def get_history(
    start: str = Query(..., description="Start of range, yyyy-mm-dd or mm/dd/yyyy"),
    end: str = Query(..., description="End of range, yyyy-mm-dd or mm/dd/yyyy"),
    customer_id: Optional[int] = Query(None, description="Limit to one customer; omit for the whole database"),
    db: Session = Depends(get_db),
):
    """Every ledger entry across (optionally) one customer or the whole
    database, within a date range, plus totals for that range — used by
    the History panel's date presets (today/week/month/year) and its
    per-day/week/month/year deposit tally."""
    try:
        start_dt = schemas.parse_flexible_date(start)
        end_dt = schemas.parse_flexible_date(end)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    query = db.query(models.Entry, models.Customer).join(
        models.Customer, models.Entry.customer_id == models.Customer.id
    )
    if customer_id is not None:
        query = query.filter(models.Entry.customer_id == customer_id)

    results = []
    total_deposit = 0.0
    total_withdrawal = 0.0
    for entry, customer in query.all():
        if not entry.date:
            continue
        try:
            entry_dt = schemas.parse_flexible_date(entry.date)
        except ValueError:
            continue
        if start_dt <= entry_dt <= end_dt:
            total_deposit += entry.deposit or 0.0
            total_withdrawal += entry.withdrawal or 0.0
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
            })

    results.sort(key=lambda r: schemas.parse_flexible_date(r["date"]), reverse=True)

    return {
        "start": start_dt.strftime("%m/%d/%Y"),
        "end": end_dt.strftime("%m/%d/%Y"),
        "customer_id": customer_id,
        "count": len(results),
        "total_deposit": total_deposit,
        "total_withdrawal": total_withdrawal,
        "net": total_deposit - total_withdrawal,
        "entries": results,
    }


# ---------------------------------------------------------------------------
# Frontend (serve the single-page app)
# ---------------------------------------------------------------------------

@app.get("/")
def serve_index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")