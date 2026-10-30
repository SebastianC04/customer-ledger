"""
Handles backing up ledger.db. Uses SQLite's own online-backup API rather
than a raw file copy, so a backup taken while the app is mid-write can't
produce a corrupted copy.

Schedule and storage rules:
- Automatic backups run once per hourly slot between 10:30 AM and 7:00 PM
  (10:30, 11:30, ... 6:30, then 7:00), only while the app is open, and only
  if the data changed since the last backup.
- Once a day is over, its backups collapse into one (the last one of that
  day), unless a restore happened that day. Restores leave a "pre-restore"
  safety backup behind (so do the date fix and deleting a customer), and a
  day that has one keeps every backup.
- Backups older than BACKUP_RETENTION_DAYS are deleted.
"""
import os
import sqlite3
from datetime import date, datetime, time as dtime, timedelta

from database import DB_PATH, get_app_data_dir

BACKUP_PREFIX = "ledger_backup_"
PRE_RESTORE_TAG = "_prerestore"
PRE_DATE_FIX_TAG = "_predatefix"
PRE_DELETE_TAG = "_predelete"
# A day holding one of these safety backups keeps all of its backups.
KEEP_DAY_TAGS = (PRE_RESTORE_TAG, PRE_DATE_FIX_TAG, PRE_DELETE_TAG)
BACKUP_RETENTION_DAYS = 30

BACKUP_WINDOW_START = dtime(10, 30)
BACKUP_WINDOW_END = dtime(19, 0)
# How late a slot may still be taken if the app was busy or just opened
# (keeps the 7:00 PM slot from being missed by a minute).
SLOT_GRACE = timedelta(minutes=59)


def get_backup_dir() -> str:
    d = os.path.join(get_app_data_dir(), "backups")
    os.makedirs(d, exist_ok=True)
    return d


def _backup_files() -> list[str]:
    """All backup filenames, oldest first (names start with a timestamp)."""
    return sorted(
        f for f in os.listdir(get_backup_dir())
        if f.startswith(BACKUP_PREFIX) and f.endswith(".db")
    )


def _timestamp_of(filename: str) -> datetime | None:
    stamp = filename[len(BACKUP_PREFIX):len(BACKUP_PREFIX) + 15]  # YYYYmmdd_HHMMSS
    try:
        return datetime.strptime(stamp, "%Y%m%d_%H%M%S")
    except ValueError:
        return None


def create_backup(tag: str = "") -> str | None:
    """Creates a timestamped backup copy of the database. Returns the
    backup file path, or None if there's no database yet to back up."""
    if not os.path.exists(DB_PATH):
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dest_path = os.path.join(get_backup_dir(), f"{BACKUP_PREFIX}{timestamp}{tag}.db")

    src_conn = sqlite3.connect(DB_PATH)
    dest_conn = sqlite3.connect(dest_path)
    try:
        with dest_conn:
            src_conn.backup(dest_conn)
    finally:
        src_conn.close()
        dest_conn.close()

    cleanup_backups()
    return dest_path


def cleanup_backups(today: date | None = None):
    """Collapses each finished day to its last backup (unless that day had a
    restore) and deletes days past the retention window."""
    today = today or date.today()
    oldest_kept = today - timedelta(days=BACKUP_RETENTION_DAYS)
    backup_dir = get_backup_dir()

    by_day: dict[date, list[str]] = {}
    for f in _backup_files():
        ts = _timestamp_of(f)
        if ts is not None:
            by_day.setdefault(ts.date(), []).append(f)

    for day, files in by_day.items():
        if day < oldest_kept:
            doomed = files
        elif day < today and not any(tag in f for f in files for tag in KEEP_DAY_TAGS):
            doomed = files[:-1]  # keep the day's final state
        else:
            doomed = []
        for f in doomed:
            os.remove(os.path.join(backup_dir, f))


def _latest_due_slot(now: datetime) -> datetime | None:
    """The most recent backup slot at or before `now`, if it's still within
    its grace period. Slots: 10:30, 11:30, ... 6:30, and 7:00 PM."""
    day = now.date()
    slots = []
    t = datetime.combine(day, BACKUP_WINDOW_START)
    end = datetime.combine(day, BACKUP_WINDOW_END)
    while t < end:
        slots.append(t)
        t += timedelta(hours=1)
    slots.append(end)

    due = [s for s in slots if s <= now]
    if not due or now - due[-1] > SLOT_GRACE:
        return None
    return due[-1]


def run_scheduled_backup(now: datetime | None = None) -> str | None:
    """Called every minute by the background thread in main.py. Takes a
    backup if a slot is due, none has been taken since that slot, and the
    data changed since the newest backup. Always tidies old backups."""
    now = now or datetime.now()
    cleanup_backups(now.date())

    slot = _latest_due_slot(now)
    if slot is None or not os.path.exists(DB_PATH):
        return None

    newest = None
    files = _backup_files()
    if files:
        newest = _timestamp_of(files[-1])
    if newest is not None:
        if newest >= slot:
            return None  # this slot is already covered
        db_modified = datetime.fromtimestamp(os.path.getmtime(DB_PATH))
        if db_modified <= newest:
            return None  # nothing changed since the last backup

    return create_backup()


def list_backups() -> list[dict]:
    backup_dir = get_backup_dir()
    result = []
    for f in reversed(_backup_files()):
        path = os.path.join(backup_dir, f)
        ts = _timestamp_of(f) or datetime.fromtimestamp(os.path.getmtime(path))
        result.append({
            "filename": f,
            "size_bytes": os.path.getsize(path),
            "created": ts.isoformat(),
            "kind": ("pre_restore" if PRE_RESTORE_TAG in f
                     else "pre_date_fix" if PRE_DATE_FIX_TAG in f
                     else "pre_delete" if PRE_DELETE_TAG in f
                     else "regular"),
        })
    return result


def _validate_backup_filename(filename: str) -> str:
    """Resolves a backup filename to a safe path inside the backup folder,
    rejecting anything that isn't actually one of our own backup files
    (blocks path traversal like '../../ledger.db' and typos alike)."""
    if not filename.startswith(BACKUP_PREFIX) or not filename.endswith(".db") \
            or os.sep in filename or "/" in filename:
        raise ValueError("Not a recognized backup filename")
    path = os.path.join(get_backup_dir(), filename)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Backup '{filename}' not found")
    return path


def restore_backup(filename: str) -> str:
    """Restores the live database from a chosen backup file.

    Takes a fresh safety backup of the *current* state first (tagged
    'prerestore', which also stops that day's backups from being collapsed)
    so restoring is itself undoable, then overwrites the live database in
    place via SQLite's backup API. Returns the filename of that pre-restore
    safety backup.
    """
    from database import engine  # imported here to avoid a circular import

    backup_path = _validate_backup_filename(filename)

    safety_backup_path = create_backup(tag=PRE_RESTORE_TAG)
    safety_backup_name = os.path.basename(safety_backup_path) if safety_backup_path else None

    # Close any pooled connections so nothing is mid-transaction against
    # the file while we overwrite it, and so the app picks up the restored
    # data on its next query rather than a stale cached connection.
    engine.dispose()

    src_conn = sqlite3.connect(backup_path)
    dest_conn = sqlite3.connect(DB_PATH)
    try:
        with dest_conn:
            src_conn.backup(dest_conn)
    finally:
        src_conn.close()
        dest_conn.close()

    return safety_backup_name


def delete_backup(filename: str):
    path = _validate_backup_filename(filename)
    os.remove(path)
