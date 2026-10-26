"""Opaque server-side sessions: disable/expiry changes take effect on every check."""
import hashlib
import re
import secrets
import threading
import time
import uuid

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from argon2.low_level import Type

from .store import Store, audit


PASSWORDS = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1, type=Type.ID)
PASSWORD_SLOTS = threading.BoundedSemaphore(4)


class AuthError(Exception):
    def __init__(self, status, code, message, retry_after=None):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.retry_after = retry_after


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def username(value):
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.@+\-]{2,63}", value):
        raise ValueError("账号须为 3–64 位英文字母、数字或 _ . @ + -")
    return value


def validate_password(value):
    if not 12 <= len(value) <= 128:
        raise ValueError("密码长度须为 12–128 个字符")


class AuthService:
    def __init__(self, settings, clock=time.time):
        self.settings, self.clock = settings, clock
        self.store = Store(settings.database)
        self.store.initialize()
        self.dummy_hash = PASSWORDS.hash(secrets.token_urlsafe(32))

    def now(self):
        return int(self.clock())

    def rate(self, buckets):
        """Count attempts before password hashing. Persist across restarts/workers."""
        now, wait = self.now(), 0
        with self.store.connect(write=True) as db:
            db.execute("DELETE FROM rate_limits WHERE started_at<=?", (now-self.settings.rate_window,))
            for key, limit in buckets:
                row = db.execute("SELECT * FROM rate_limits WHERE bucket=?", (key,)).fetchone()
                if row and row["attempts"] >= limit:
                    wait = max(wait, row["started_at"] + self.settings.rate_window - now)
            if not wait:
                for key, _ in buckets:
                    db.execute("INSERT INTO rate_limits VALUES(?,?,1) ON CONFLICT(bucket) "
                               "DO UPDATE SET attempts=attempts+1", (key, now))
        if wait:
            raise AuthError(429, "rate_limited", "请求过于频繁，请稍后重试", wait)

    def _account(self, user):
        if not user or not user["enabled"]:
            raise AuthError(403, "account_disabled", "账号已停用，请联系管理员")
        if user["expires_at"] is not None and user["expires_at"] <= self.now():
            raise AuthError(403, "account_expired", "账号使用期限已到，请联系管理员")

    @staticmethod
    def public_user(user):
        return {k: user[k] for k in ("id", "username", "expires_at", "max_devices")}

    def _tokens(self):
        return secrets.token_urlsafe(32), secrets.token_urlsafe(48)

    def _result(self, user, session, access, refresh):
        return {"token_type": "Bearer", "access_token": access, "refresh_token": refresh,
                "access_expires_at": session["access_expires_at"],
                "refresh_expires_at": session["expires_at"], "session_id": session["id"],
                "server_time": self.now(), "check_after_seconds": 60,
                "user": self.public_user(user)}

    def login(self, name, password, device_id, device_name, ip):
        name = username(name)
        self.rate([("login:ip:"+digest(ip), self.settings.login_ip_limit),
                   ("login:user:"+digest(name), self.settings.login_user_limit)])
        with self.store.connect() as db:
            user = db.execute("SELECT * FROM users WHERE username=?", (name,)).fetchone()
        if not PASSWORD_SLOTS.acquire(blocking=False):
            raise AuthError(503, "auth_busy", "登录服务繁忙，请稍后重试", 3)
        try:
            encoded = user["password_hash"] if user else self.dummy_hash
            try:
                matched = PASSWORDS.verify(encoded, password)
            except (VerificationError, InvalidHashError):
                matched = False
            new_hash = PASSWORDS.hash(password) if matched and PASSWORDS.check_needs_rehash(encoded) else None
        finally:
            PASSWORD_SLOTS.release()
        if not matched or not user:
            raise AuthError(401, "invalid_credentials", "账号或密码不正确")
        now = self.now()
        access, refresh = self._tokens()
        sid, device = str(uuid.uuid4()), digest(device_id)
        with self.store.connect(write=True) as db:
            current = db.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
            # An administrator may reset the password while Argon2 is running.
            if not current or current["password_hash"] != user["password_hash"]:
                raise AuthError(401, "invalid_credentials", "账号信息已更新，请重新登录")
            self._account(current)
            count = db.execute("SELECT COUNT(DISTINCT device_hash) FROM sessions "
                               "WHERE user_id=? AND revoked_at IS NULL AND expires_at>? AND device_hash<>?",
                               (user["id"], now, device)).fetchone()[0]
            if current["max_devices"] and count >= current["max_devices"]:
                raise AuthError(403, "device_limit", "已达到登录设备数量上限，请退出其他设备或联系管理员")
            db.execute("UPDATE sessions SET revoked_at=? WHERE user_id=? AND device_hash=? "
                       "AND revoked_at IS NULL", (now, user["id"], device))
            db.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?,?,NULL)",
                       (sid, user["id"], device, device_name, digest(access), digest(refresh),
                        now+self.settings.access_seconds, now+self.settings.session_seconds, now, now))
            if new_hash:
                db.execute("UPDATE users SET password_hash=? WHERE id=?", (new_hash, user["id"]))
            audit(db, now, "login", user["id"], sid)
            session = db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        return self._result(current, session, access, refresh)

    def verify(self, token, device_id):
        now = self.now()
        with self.store.connect() as db:
            session = db.execute("SELECT * FROM sessions WHERE access_hash=?", (digest(token),)).fetchone()
            if (not session or session["revoked_at"] is not None or session["expires_at"] <= now
                    or session["device_hash"] != digest(device_id)):
                raise AuthError(401, "invalid_session", "登录状态无效，请重新登录")
            if session["access_expires_at"] <= now:
                raise AuthError(401, "access_expired", "登录凭证需续期")
            user = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
            self._account(user)
        return {"valid": True, "user": self.public_user(user), "session_id": session["id"],
                "access_expires_at": session["access_expires_at"], "server_time": now,
                "check_after_seconds": 60}

    def refresh(self, token, device_id, ip):
        self.rate([("refresh:ip:"+digest(ip), 120)])
        now, replayed = self.now(), False
        access, refresh = self._tokens()
        with self.store.connect(write=True) as db:
            session = db.execute("SELECT * FROM sessions WHERE refresh_hash=?", (digest(token),)).fetchone()
            if not session:
                used = db.execute("SELECT s.* FROM used_refresh r JOIN sessions s ON s.id=r.session_id "
                                  "WHERE r.token_hash=?", (digest(token),)).fetchone()
                if used and used["device_hash"] == digest(device_id):
                    db.execute("UPDATE sessions SET revoked_at=? WHERE id=?", (now, used["id"]))
                    audit(db, now, "refresh_replay_revoked", used["user_id"], used["id"])
                    replayed = True
                else:
                    raise AuthError(401, "invalid_session", "登录状态无效，请重新登录")
            else:
                if (session["revoked_at"] is not None or session["expires_at"] <= now
                        or session["device_hash"] != digest(device_id)):
                    raise AuthError(401, "invalid_session", "登录状态无效，请重新登录")
                user = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
                self._account(user)
                db.execute("INSERT INTO used_refresh VALUES(?,?)", (digest(token), session["id"]))
                db.execute("UPDATE sessions SET access_hash=?,refresh_hash=?,access_expires_at=?,last_seen_at=? "
                           "WHERE id=?", (digest(access), digest(refresh),
                                         min(now+self.settings.access_seconds, session["expires_at"]), now, session["id"]))
                session = db.execute("SELECT * FROM sessions WHERE id=?", (session["id"],)).fetchone()
        if replayed:
            # Raise only after COMMIT so replay revocation is not rolled back.
            raise AuthError(401, "refresh_reused", "续期凭证已使用，该登录已撤销，请重新登录")
        return self._result(user, session, access, refresh)

    def logout(self, token, device_id):
        now = self.now()
        with self.store.connect(write=True) as db:
            session = db.execute("SELECT * FROM sessions WHERE refresh_hash=?", (digest(token),)).fetchone()
            if not session:
                session = db.execute("SELECT s.* FROM used_refresh r JOIN sessions s ON s.id=r.session_id "
                                     "WHERE r.token_hash=?", (digest(token),)).fetchone()
            if session and session["device_hash"] == digest(device_id) and session["revoked_at"] is None:
                db.execute("UPDATE sessions SET revoked_at=? WHERE id=?", (now, session["id"]))
                audit(db, now, "logout", session["user_id"], session["id"])
        return {"ok": True}

    def create_user(self, name, password, expires_at=None, max_devices=0):
        name = username(name)
        validate_password(password)
        if max_devices < 0:
            raise ValueError("设备上限不能为负数，0 表示不限")
        encoded = PASSWORDS.hash(password)
        uid = str(uuid.uuid4())
        with self.store.connect(write=True) as db:
            db.execute("INSERT INTO users VALUES(?,?,?,1,?,?,?)",
                       (uid, name, encoded, expires_at, max_devices, self.now()))
            audit(db, self.now(), "admin_create", uid)
        return uid

    def manage_user(self, name, *, enabled=None, expires_at="unchanged", max_devices=None,
                    password=None, revoke=False):
        if password is not None:
            validate_password(password)
        if max_devices is not None and max_devices < 0:
            raise ValueError("设备上限不能为负数")
        encoded = PASSWORDS.hash(password) if password is not None else None
        with self.store.connect(write=True) as db:
            user = db.execute("SELECT * FROM users WHERE username=?", (username(name),)).fetchone()
            if not user:
                raise ValueError("账号不存在")
            updates = {}
            if enabled is not None:
                updates["enabled"] = int(enabled)
            if expires_at != "unchanged":
                updates["expires_at"] = expires_at
            if max_devices is not None:
                updates["max_devices"] = max_devices
            if encoded:
                updates["password_hash"] = encoded
            for key, value in updates.items():  # Column names are constants above, never user input.
                db.execute(f"UPDATE users SET {key}=? WHERE id=?", (value, user["id"]))
            if revoke or encoded or enabled is False or max_devices is not None:
                db.execute("UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                           (self.now(), user["id"]))
            audit(db, self.now(), "admin_update", user["id"])

    def prune(self):
        now = self.now()
        with self.store.connect(write=True) as db:
            db.execute("DELETE FROM sessions WHERE expires_at<=?", (now,))
            db.execute("DELETE FROM rate_limits WHERE started_at<=?", (now-self.settings.rate_window,))
            db.execute("DELETE FROM audit WHERE at<?", (now-90*86400,))
            db.execute('DELETE FROM admin_sessions WHERE expires_at<=?', (now,))
            db.execute('DELETE FROM admin_audit WHERE at<?', (now-90*86400,))
            from .diagnostics import prune
            prune(db, now)
