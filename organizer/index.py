"""Local SQLite index. Lives in the state dir (default ~/.file-organizer)."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id          INTEGER PRIMARY KEY,
    started_at  REAL NOT NULL,
    finished_at REAL,
    roots       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
    id          INTEGER PRIMARY KEY,
    scan_id     INTEGER NOT NULL REFERENCES scans(id),
    root        TEXT NOT NULL,
    path        TEXT NOT NULL,
    name        TEXT NOT NULL,
    ext         TEXT NOT NULL,
    kind        TEXT NOT NULL,
    size        INTEGER NOT NULL,
    mtime       REAL NOT NULL,
    atime       REAL,
    birthtime   REAL,
    device      INTEGER NOT NULL,
    inode       INTEGER NOT NULL,
    item_type   TEXT NOT NULL,  -- file | package | project | symlink
    sensitive   INTEGER NOT NULL DEFAULT 0,
    dataless    INTEGER NOT NULL DEFAULT 0,  -- iCloud cloud-only placeholder
    business    INTEGER NOT NULL DEFAULT 0,
    base_name   TEXT NOT NULL,
    quick_hash  TEXT,
    full_hash   TEXT,
    UNIQUE (scan_id, path)
);
CREATE INDEX IF NOT EXISTS idx_files_scan_size ON files(scan_id, size);
CREATE INDEX IF NOT EXISTS idx_files_scan_hash ON files(scan_id, full_hash);
CREATE INDEX IF NOT EXISTS idx_files_scan_base ON files(scan_id, base_name);
CREATE TABLE IF NOT EXISTS skipped (
    scan_id INTEGER NOT NULL REFERENCES scans(id),
    path    TEXT NOT NULL,
    reason  TEXT NOT NULL
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Index contains file paths, which can be sensitive: owner-only access.
    os.chmod(db_path.parent, 0o700)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    os.chmod(db_path, 0o600)
    return conn


def latest_scan_id(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT id FROM scans WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise LookupError("no completed scan in the index — run `audit` first")
    return int(row["id"])
