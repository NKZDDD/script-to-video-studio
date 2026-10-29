from contextlib import contextmanager
from pathlib import Path
import os
import sqlite3


SCHEMA = """
CREATE TABLE IF NOT EXISTS adapter_bundles (
    sha256 TEXT PRIMARY KEY, version TEXT NOT NULL, providers TEXT NOT NULL,
    payload TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS adapter_policy (
    id INTEGER PRIMARY KEY CHECK(id=1), sha256 TEXT NOT NULL REFERENCES adapter_bundles(sha256)
);
CREATE TABLE IF NOT EXISTS client_version_policy (
    id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL CHECK(enabled IN (0,1))
);
INSERT OR IGNORE INTO client_version_policy VALUES(1,1);
CREATE TABLE IF NOT EXISTS application_version_policy (
    application TEXT PRIMARY KEY CHECK(application IN ('studio','batch')),
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1))
);
INSERT OR IGNORE INTO application_version_policy SELECT 'studio',enabled FROM client_version_policy WHERE id=1;
INSERT OR IGNORE INTO application_version_policy SELECT 'batch',enabled FROM client_version_policy WHERE id=1;
CREATE TABLE IF NOT EXISTS client_releases (
    flavor TEXT PRIMARY KEY, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS client_builds (
    flavor TEXT NOT NULL, version TEXT NOT NULL, build_sha256 TEXT NOT NULL,
    PRIMARY KEY(flavor,version)
);
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
CREATE TABLE IF NOT EXISTS client_observations (
    user_id TEXT NOT NULL REFERENCES users(id), device_hash TEXT NOT NULL,
    flavor TEXT NOT NULL, version TEXT NOT NULL, build_sha256 TEXT NOT NULL,
    device_name TEXT NOT NULL, first_seen_at INTEGER NOT NULL, last_seen_at INTEGER NOT NULL,
    PRIMARY KEY(user_id,device_hash,flavor)
);
CREATE INDEX IF NOT EXISTS client_seen ON client_observations(last_seen_at);
CREATE TABLE IF NOT EXISTS r2_settings (
    id INTEGER PRIMARY KEY CHECK(id=1), config TEXT NOT NULL,
    revision TEXT NOT NULL, enabled INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS user_r2 (
    user_id TEXT PRIMARY KEY REFERENCES users(id), enabled INTEGER NOT NULL
);
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
            from .diagnostics import SCHEMA as DIAGNOSTIC_SCHEMA
            db.executescript(DIAGNOSTIC_SCHEMA)
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

    def set_client_version_check(self, enabled, actor, now, application='studio'):
        if application not in ('studio', 'batch'):
            raise ValueError('未知应用')
        with self.connect(write=True) as db:
            db.execute("UPDATE application_version_policy SET enabled=? WHERE application=?", (int(enabled), application))
            db.execute("INSERT INTO admin_audit(at,administrator,action,target) VALUES(?,?,?,?)",
                       (now, actor, "client_version_check", application + ':' + ("enabled" if enabled else "paused")))


def application_for_flavor(flavor):
    return 'batch' if flavor == 'batch' else 'studio'


def client_version_check_enabled(db, application='studio'):
    row = db.execute("SELECT enabled FROM application_version_policy WHERE application=?", (application,)).fetchone()
    return row is None or bool(row["enabled"])


def audit(db, now, event, user_id=None, session_id=None):
    db.execute("INSERT INTO audit(at,event,user_id,session_id) VALUES(?,?,?,?)",
               (now, event, user_id, session_id))
