"""Latest authenticated client observations, not telemetry from anonymous requests."""
from collections import defaultdict
import json
import re
from typing import Literal

from fastapi import APIRouter, Query, Request

from .admin import current
from .service import digest
from .store import client_version_check_enabled


router = APIRouter()
FLAVORS = ("all", "v34", "v61", "batch")
VERSION = re.compile(r"[0-9]{1,5}(?:\.[0-9]{1,5}){3}")
BUILD = re.compile(r"[a-f0-9]{64}")


def observe(request, result, device_id):
    """Call only after successful account/session authentication and version gates."""
    request.state._diag_user = result['user']['id']
    request.state._diag_username = result['user']['username']
    auth = request.app.state.auth
    flavor = request.headers.get("x-studio-flavor", "")
    flavor = flavor if flavor in FLAVORS else "unknown"
    version = request.headers.get("x-studio-version", "")
    version = version if VERSION.fullmatch(version) else ""
    build = request.headers.get("x-studio-build", "")
    build = build if BUILD.fullmatch(build) else ""
    now, device, uid = auth.now(), digest(device_id), result["user"]["id"]
    with auth.store.connect(write=True) as db:
        # Recheck the session to avoid recording a concurrently revoked login.
        session = db.execute("SELECT device_name FROM sessions WHERE id=? AND user_id=? "
                             "AND device_hash=? AND revoked_at IS NULL AND expires_at>?",
                             (result["session_id"], uid, device, now)).fetchone()
        if not session:
            return
        db.execute("INSERT INTO client_observations VALUES(?,?,?,?,?,?,?,?) "
                   "ON CONFLICT(user_id,device_hash,flavor) DO UPDATE SET "
                   "version=excluded.version,build_sha256=excluded.build_sha256,"
                   "device_name=excluded.device_name,last_seen_at=excluded.last_seen_at",
                   (uid, device, flavor, version, build, session["device_name"], now, now))


def classify(row, releases, builds):
    release = releases.get(row["flavor"], {})
    numbers = lambda v: tuple(map(int, v.split(".")))
    v = row["version"]
    known = row["flavor"] != "unknown" and bool(v)
    recommended, minimum = release.get("version"), release.get("minimum_version", "0.0.0.0")
    old = bool(known and recommended and numbers(v) < numbers(recommended))
    required = bool(known and numbers(v) < numbers(minimum))
    expected = builds.get((row["flavor"], v))
    if not known or not row["build_sha256"]:
        state = "unknown"
    elif not expected:
        state = "unregistered"
    elif row["build_sha256"] != expected:
        state = "mismatch"
    elif required:
        state = "required"
    elif old:
        state = "outdated"
    elif not recommended:
        state = "unpublished"
    elif numbers(v) > numbers(recommended):
        state = "ahead"
    else:
        state = "current"
    return dict(state=state, old=old, required=required,
                attention=state in ("unknown", "unregistered", "mismatch"))


@router.get("/admin/api/releases/statistics")
def statistics(request: Request, days: Literal["7", "30", "90", "0"] = "30",
               flavor: Literal["*", "all", "v34", "v61", "batch", "unknown"] = "*",
               segment: Literal["all", "old", "required", "attention"] = "all",
               version: str = Query(default="", max_length=24),
               search: str = Query(default="", max_length=64),
               page: int = Query(default=1, ge=1, le=1000000)):
    current(request)
    auth = request.app.state.auth
    days = int(days)
    now, limit = auth.now(), 50
    cutoff = now-days*86400 if days else 0
    with auth.store.connect() as db:
        # One read snapshot keeps counts and policy consistent during publication.
        db.execute("BEGIN")
        users = db.execute("SELECT id,enabled,expires_at FROM users").fetchall()
        eligible = {u["id"] for u in users if u["enabled"] and (u["expires_at"] is None or u["expires_at"] > now)}
        rows = db.execute("SELECT o.*,u.username FROM client_observations o JOIN users u ON u.id=o.user_id "
                          "ORDER BY o.last_seen_at DESC,o.user_id,o.device_hash,o.flavor").fetchall()
        releases = {r["flavor"]: json.loads(r["payload"]) for r in db.execute("SELECT * FROM client_releases")}
        builds = {(r["flavor"], r["version"]): r["build_sha256"] for r in db.execute("SELECT * FROM client_builds")}
        enabled = client_version_check_enabled(db)
    observed = {r["user_id"] for r in rows if r["user_id"] in eligible}
    candidates = [r for r in rows if r["user_id"] in eligible and (flavor == "*" or r["flavor"] == flavor)]
    active = [r for r in candidates if r["last_seen_at"] >= cutoff]
    account_sets = {k: set() for k in ("accounts", "old", "required", "attention")}
    groups = defaultdict(lambda: {"accounts": set(), "installations": 0, "last_seen_at": 0})
    details = []
    for row in active:
        flags = classify(row, releases, builds)
        uid = row["user_id"]
        account_sets["accounts"].add(uid)
        for k in ("old", "required", "attention"):
            if flags[k]:
                account_sets[k].add(uid)
        group = groups[(row["flavor"], row["version"], flags["state"])]
        group["accounts"].add(uid)
        group["installations"] += 1
        group["last_seen_at"] = max(group["last_seen_at"], row["last_seen_at"])
        if (segment != "all" and not flags[segment]) or (version and (row["version"] or "unknown") != version):
            continue
        if search.casefold() not in row["username"].casefold():
            continue
        details.append({**{k: row[k] for k in ("username", "device_name", "flavor", "version", "last_seen_at")},
                        # A short, account-scoped device label; never expose hardware IDs or tokens.
                        "device_label": digest(uid+":"+row["device_hash"])[:12], **flags})
    distribution = [{"flavor": f, "version": v, "state": state, **g, "accounts": len(g["accounts"])}
                    for (f, v, state), g in groups.items()]
    distribution.sort(key=lambda r: (-r["installations"], r["flavor"], r["version"], r["state"]))
    total = len(details)
    page = min(page, max(1, (total+limit-1)//limit))
    return {"as_of": now, "days": days, "flavor": flavor, "enforcement_enabled": enabled,
            "summary": {**{k: len(v) for k, v in account_sets.items()}, "installations": len(active),
                        "inactive_accounts": len({r["user_id"] for r in candidates}-account_sets["accounts"]),
                        "eligible_accounts": len(eligible), "unobserved_accounts": len(eligible-observed),
                        "excluded_accounts": len(users)-len(eligible)},
            "distribution": distribution, "details": details[(page-1)*limit:page*limit],
            "total": total, "page": page, "page_size": limit}
