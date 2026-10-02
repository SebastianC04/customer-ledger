import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import os


def get_app_data_dir() -> str:
    """Where the database and backups live.

    - Running from source (normal `python`/`uvicorn` during development):
      same folder this file is in, exactly as before — no change for the
      dev workflow, no migration needed.
    - Running as a bundled PyInstaller .exe: a proper per-user data folder
      outside the app's install location, because a bundled exe unpacks
      itself into a fresh temp folder on every launch, so anything stored
      next to the code would vanish the moment the app closes.
    """
    if getattr(sys, "frozen", False):
        if os.name == "nt":
            base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        elif sys.platform == "darwin":
            base = os.path.expanduser("~/Library/Application Support")
        else:
            base = os.path.expanduser("~/.local/share")
        data_dir = os.path.join(base, "CustomerLedger")
    else:
        data_dir = os.path.dirname(os.path.abspath(__file__))

    os.makedirs(data_dir, exist_ok=True)
    return data_dir


DB_PATH = os.path.join(get_app_data_dir(), "ledger.db")
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_migrations():
    """Lightweight, idempotent migration: adds any columns the current
    models.py expects but an older ledger.db doesn't have yet.

    SQLAlchemy's create_all() only creates missing *tables*, not missing
    *columns* on tables that already exist — so without this, opening an
    older database file with a newer version of the app would crash the
    moment it touched a column that didn't exist yet. Safe to call on
    every startup: it's a no-op once the columns are already there.
    """
    from sqlalchemy import text

    # (table, column, SQL type, default expression for existing rows)
    expected_columns = [
        ("customers", "time_created", "TEXT", None),
        ("customers", "language", "TEXT", None),
        ("entries", "time", "TEXT", None),
    ]

    with engine.connect() as conn:
        for table, column, col_type, default in expected_columns:
            existing = [row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))]
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
        conn.commit()