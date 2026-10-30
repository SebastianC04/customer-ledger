"""
Fills the local development database (backend/ledger.db) with a large set of
invented customers and ledger entries, for trying out the app.

Writes straight to the database instead of going through the API, because
the API stamps every new entry with the current time, and realistic times
of day are needed to try the History time filter.

What it creates (all names and phone numbers are made up, 555 numbers):
  - 120 customers created over the past year, as if by an older version of
    the app (no hidden creation timestamp); about a third were "moved over
    from paper" and have a starting balance
  - ~2,000 entries between 10 AM and 7 PM, always after the card was made,
    busier in the last month, with plenty of activity today and this week
  - 6 entries and 4 cards with the old after-5 PM date mistake, so the
    "Fix Wrong Dates" notice has something to show

Usage (from the repo root; the app can be running or not):
    python seed_demo_data.py
Run it again to add another batch. To start over, stop the app and delete
backend/ledger.db first.
"""
import os
import random
import sys
from datetime import date, datetime, time, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))

import models  # noqa: E402
from database import Base, SessionLocal, engine, run_migrations  # noqa: E402

rng = random.Random(2026)

FIRST = [
    "José", "María", "Juan", "Guadalupe", "Luis", "Rosa", "Carlos", "Ana", "Miguel",
    "Elena", "Jorge", "Patricia", "Francisco", "Laura", "Antonio", "Carmen", "Pedro",
    "Sofía", "Ricardo", "Isabel", "Daniel", "Gabriela", "Alejandro", "Teresa", "Raúl",
    "Verónica", "Fernando", "Lucía", "Javier", "Adriana", "John", "Mike", "Sarah",
    "David", "Linda", "James", "Karen", "Robert", "Lisa", "Tony",
]
LAST = [
    "García", "Hernández", "Martínez", "López", "González", "Pérez", "Rodríguez",
    "Sánchez", "Ramírez", "Torres", "Flores", "Rivera", "Gómez", "Díaz", "Cruz",
    "Morales", "Reyes", "Gutiérrez", "Ortiz", "Mendoza", "Castillo", "Vargas",
    "Romero", "Herrera", "Medina", "Aguilar", "Smith", "Johnson", "Brown", "Tovar",
]
STREETS = ["Main St", "Oak Ave", "International Blvd", "Fruitvale Ave", "E 14th St",
           "MacArthur Blvd", "Foothill Blvd", "Mission St", "High St", "Seminary Ave"]
CITIES = ["Oakland", "San Leandro", "Hayward", "Fremont", "Union City"]

# (description, withdrawal range) — what a customer takes home on their card
ITEMS = [
    ("camisa vaquera", 35, 65), ("camisa futbol", 25, 45), ("bota de trabajo", 90, 180),
    ("bota vaquera", 120, 350), ("joyas", 40, 400), ("perfumes", 30, 90),
    ("sombrero", 25, 80), ("texana", 60, 250), ("cinto piteado", 45, 120),
]
OPEN, CLOSE = time(10, 0), time(19, 0)
TODAY = date.today()
NOW = datetime.now()


def money(lo, hi, step=5):
    return float(rng.randrange(lo // step, hi // step + 1) * step)


def fmt_date(d):
    return d.strftime("%m/%d/%Y")


def fmt_time(t):
    s = datetime.combine(TODAY, t).strftime("%I:%M %p")
    return s[1:] if s[0] == "0" else s


def to_minutes(t):
    return t.hour * 60 + t.minute


def from_minutes(m):
    return time(m // 60, m % 60)


def random_time_between(lo, hi):
    return from_minutes(rng.randrange(to_minutes(lo), to_minutes(hi) + 1))


def local_to_utc_naive(d, t):
    """created_at is stored in UTC without a time zone, like the app does."""
    return datetime.combine(d, t).astimezone().astimezone(timezone.utc).replace(tzinfo=None)


def latest_allowed(day):
    """Closing time, or right now if it's today."""
    if day == TODAY and NOW.time() < CLOSE:
        return NOW.time().replace(second=0, microsecond=0)
    return CLOSE


def random_entry_day(created):
    """Visits lean recent: half in the last 30 days, a good share this week."""
    span = (TODAY - created).days
    roll = rng.random()
    if roll < 0.15:
        back = rng.randrange(0, min(span, 6) + 1)
    elif roll < 0.5:
        back = rng.randrange(0, min(span, 30) + 1)
    else:
        back = rng.randrange(0, span + 1)
    return TODAY - timedelta(days=back)


def make_customers(n):
    """Returns (customer, created_day, created_time) tuples."""
    used_names, used_phones = set(), set()
    out = []
    while len(out) < n:
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        if name in used_names:
            continue
        used_names.add(name)
        phone = None
        while phone is None or phone in used_phones:
            phone = f"({rng.choice(['510', '415', '925', '408'])})-555-{rng.randrange(100, 10000):04d}"
        used_phones.add(phone)

        created_day = TODAY - timedelta(days=rng.randrange(1, 365))
        created_time = random_time_between(OPEN, time(18, 30))
        transferred = rng.random() < 0.35
        customer = models.Customer(
            name=name,
            phone_number=phone,
            address=(f"{rng.randrange(100, 9999)} {rng.choice(STREETS)}, {rng.choice(CITIES)}"
                     if rng.random() < 0.7 else None),
            date_created=fmt_date(created_day),
            time_created=fmt_time(created_time),
            language="es" if rng.random() < 0.25 else None,
            opening_balance=money(-400, 300) if transferred else 0.0,
        )
        out.append((customer, created_day, created_time))
    return out


def make_visits(created_day, created_time, first_same_evening=False):
    """(day, time) visits, all after the card was made, oldest first."""
    count = rng.choice([2, 4, 6, 10, 15, 20, 30, 40])
    visits = []
    if first_same_evening:
        start = from_minutes(to_minutes(created_time) + 5)
        visits.append((created_day, random_time_between(start, from_minutes(to_minutes(start) + 15))))
        count -= 1
    while len(visits) < count:
        day = random_entry_day(created_day)
        lo = from_minutes(to_minutes(created_time) + 1) if day == created_day else OPEN
        hi = latest_allowed(day)
        if lo > hi:
            continue  # nothing possible that day; pick another
        visits.append((day, random_time_between(lo, hi)))
    return sorted(visits)


def main():
    Base.metadata.create_all(bind=engine)
    run_migrations()
    db = SessionLocal()
    try:
        made = make_customers(120)

        # Recreate the old bug on 4 cards: made after 5 PM, stored with the
        # next day's date, and first item added that same evening (which is
        # how the app can confirm the date is wrong).
        bugged_ids = {id(c) for c, day, _ in rng.sample(
            [m for m in made if 7 <= (TODAY - m[1]).days <= 90], 4)}
        for i, (c, day, _) in enumerate(made):
            if id(c) in bugged_ids:
                late = random_time_between(time(17, 0), time(18, 30))
                made[i] = (c, day, late)
                c.time_created = fmt_time(late)

        db.add_all([c for c, _, _ in made])
        db.flush()  # assign ids
        # The column default fills in "now" on insert even when given None,
        # so clear it afterwards to look like cards from an older version.
        for c, _, _ in made:
            c.created_at = None

        all_entries = []
        for c, created_day, created_time in made:
            visits = make_visits(created_day, created_time, first_same_evening=id(c) in bugged_ids)
            balance = c.opening_balance or 0.0
            for order, (day, t) in enumerate(visits, start=1):
                if rng.random() < 0.55:
                    desc, dep, wd = "Abono", money(10, 200), 0.0
                else:
                    desc, lo, hi = rng.choice(ITEMS)
                    dep, wd = 0.0, money(lo, hi)
                balance += dep - wd
                e = models.Entry(
                    customer_id=c.id, date=fmt_date(day), time=fmt_time(t),
                    item_description=desc, deposit=dep, withdrawal=wd,
                    total_balance=balance, sort_order=order,
                    created_at=local_to_utc_naive(day, t),
                )
                db.add(e)
                all_entries.append((e, created_day))
            if id(c) in bugged_ids:
                c.date_created = fmt_date(created_day + timedelta(days=1))

        # Recreate the old bug on 6 late entries: the stored date is the next
        # day (what UTC said), while created_at keeps the true moment.
        candidates = [
            e for e, created_day in all_entries
            if 7 <= (TODAY - datetime.strptime(e.date, "%m/%d/%Y").date()).days <= 60
            and datetime.strptime(e.date, "%m/%d/%Y").date() > created_day
        ]
        bugged_entries = rng.sample(candidates, min(6, len(candidates)))
        for e in bugged_entries:
            day = datetime.strptime(e.date, "%m/%d/%Y").date()
            t = random_time_between(time(17, 0), CLOSE)
            e.time = fmt_time(t)
            e.created_at = local_to_utc_naive(day, t)
            e.date = fmt_date(day + timedelta(days=1))

        db.commit()
        print(f"Added {len(made)} customers and {len(all_entries)} entries to {engine.url.database}")
        print(f"Old date mistake planted on {len(bugged_entries)} entries and {len(bugged_ids)} cards")
    finally:
        db.close()


if __name__ == "__main__":
    main()
