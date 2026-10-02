"""
Handles backing up ledger.db. Uses SQLite's own online-backup API rather
than a raw file copy, so a backup taken while the app is mid-write can't
produce a corrupted copy.
"""
import os
import sqlite3
from datetime import datetime

from database import DB_PATH, get_app_data_dir

BACKUP_RETENTION = 20  # keep this many most-recent backups, delete older ones
BACKUP_PREFIX = "ledger_backup_"


def get_backup_dir() -> str:
    d = os.path.join(get_app_data_dir(), "backups")
    os.makedirs(d, exist_ok=True)
    return d


def create_backup() -> str | None:
    """Creates a timestamped backup copy of the database. Returns the
    backup file path, or None if there's no database yet to back up."""
    if not os.path.exists(DB_PATH):
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dest_path = os.path.join(get_backup_dir(), f"{BACKUP_PREFIX}{timestamp}.db")

    src_conn = sqlite3.connect(DB_PATH)
    dest_conn = sqlite3.connect(dest_path)
    try:
        with dest_conn:
            src_conn.backup(dest_conn)
    finally:
        src_conn.close()
        dest_conn.close()

    _rotate_backups()
    return dest_path


def _rotate_backups():
    backup_dir = get_backup_dir()
    files = sorted(
        f for f in os.listdir(backup_dir)
        if f.startswith(BACKUP_PREFIX) and f.endswith(".db")
    )
    while len(files) > BACKUP_RETENTION:
        oldest = files.pop(0)
        os.remove(os.path.join(backup_dir, oldest))


def list_backups() -> list[dict]:
    backup_dir = get_backup_dir()
    files = sorted(
        (f for f in os.listdir(backup_dir)
         if f.startswith(BACKUP_PREFIX) and f.endswith(".db")),
        reverse=True,
    )
    result = []
    for f in files:
        path = os.path.join(backup_dir, f)
        result.append({
            "filename": f,
            "size_bytes": os.path.getsize(path),
            "created": datetime.fromtimestamp(os.path.getmtime(path)).isoformat(),
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
    'pre_restore') so restoring is itself undoable, then overwrites the
    live database in place via SQLite's backup API. Returns the filename
    of that pre-restore safety backup.
    """
    from database import engine  # imported here to avoid a circular import

    backup_path = _validate_backup_filename(filename)

    # Safety net: preserve current state before overwriting it, in case
    # this restore turns out to be a mistake.
    safety_backup_path = create_backup()
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
