from contextlib import contextmanager
from pathlib import Path
import os
import sqlite3


SCHEMA = """
CREATE TABLE IF NOT EXISTS key_library (
    id TEXT PRIMARY KEY, provider TEXT NOT NULL, name TEXT NOT NULL,
    config TEXT NOT NULL, revision TEXT NOT NULL, enabled INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS user_keys (
    user_id TEXT NOT NULL REFERENCES users(id), key_id TEXT NOT NULL REFERENCES key_library(id),
    position INTEGER NOT NULL, PRIMARY KEY(user_id,key_id)
);
CREATE TABLE IF NOT EXISTS provider_assignments (
    user_id TEXT NOT NULL REFERENCES users(id), provider TEXT NOT NULL,
    config TEXT NOT NULL, revision TEXT NOT NULL, updated_at INTEGER NOT NULL,
    PRIMARY KEY(user_id,provider)
);
CREATE TABLE IF NOT EXISTS usage_reports (
    user_id TEXT NOT NULL REFERENCES users(id), event_id TEXT NOT NULL,
    sequence INTEGER NOT NULL, payload TEXT NOT NULL, received_at INTEGER NOT NULL,
    PRIMARY KEY(user_id,event_id)
);
CREATE INDEX IF NOT EXISTS usage_received ON usage_reports(user_id,received_at);
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
    expires_at INTEGER, max_devices INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
    device_hash TEXT NOT NULL, device_name TEXT NOT NULL,
    access_hash TEXT NOT NULL UNIQUE, refresh_hash TEXT NOT NULL UNIQUE,
    access_expires_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL, last_seen_at INTEGER NOT NULL,
    revoked_at INTEGER
);
CREATE INDEX IF NOT EXISTS session_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS used_refresh (
    token_hash TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS rate_limits (
    bucket TEXT PRIMARY KEY, started_at INTEGER NOT NULL, attempts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY, at INTEGER NOT NULL,
    event TEXT NOT NULL, user_id TEXT, session_id TEXT
);
CREATE TABLE IF NOT EXISTS administrators (
    username TEXT PRIMARY KEY, password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS admin_sessions (
    token_hash TEXT PRIMARY KEY, username TEXT NOT NULL REFERENCES administrators(username),
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS admin_audit (
    id INTEGER PRIMARY KEY, at INTEGER NOT NULL, administrator TEXT NOT NULL,
    action TEXT NOT NULL, target TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path):
        self.path = Path(path).resolve()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError("Database is newer than this program; refusing to start")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            db.execute("PRAGMA user_version=1")
        from .key_library import migrate
        with self.connect(write=True) as db:
            migrate(db)
        if os.name == "posix":
            self.path.chmod(0o600)

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def backup(self, target):
        target = Path(target).resolve()
        # Never overwrite an existing backup or the live database.
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with self.connect() as source:
            dest = sqlite3.connect(target)
            try:
                source.backup(dest)
            finally:
                dest.close()


def audit(db, now, event, user_id=None, session_id=None):
    db.execute("INSERT INTO audit(at,event,user_id,session_id) VALUES(?,?,?,?)",
               (now, event, user_id, session_id))
