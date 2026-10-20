"""Small release catalog. Large installers are served by Nginx /downloads/."""
import json
import re
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .admin import WEB, current, record
from .service import AuthError

router = APIRouter()


class Release(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: str = Field(pattern=r"^\d{1,5}(\.\d{1,5}){3}$")
    flavor: str = Field(pattern=r"^(all|v34|v61)$")
    url: str = Field(max_length=2048)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bytes: int = Field(gt=0, le=2 * 1024**3)
    notes: str = Field(default="", max_length=5000)
    build_sha256: str = Field(default="", pattern=r"^([a-f0-9]{64})?$")
    mandatory: bool = False

    @field_validator("url")
    @classmethod
    def validate_url(cls, value):
        u = urlparse(value)
        if (u.scheme != "https" or not u.hostname or u.username or u.password or
                u.query or u.fragment or not re.fullmatch(r"/downloads/[A-Za-z0-9_.-]+\.exe", u.path)):
            raise ValueError("请使用本站 HTTPS /downloads/安装包.exe 地址（文件名使用英文和数字）")
        return value


@router.get("/admin/releases.js")
def script():
    return FileResponse(WEB / "releases.js", media_type="text/javascript")


@router.get("/admin/api/releases")
def list_releases(request: Request):
    current(request)
    with request.app.state.auth.store.connect() as db:
        rows = db.execute("SELECT payload FROM client_releases ORDER BY flavor").fetchall()
    return {"releases": [json.loads(row["payload"]) for row in rows]}


@router.post("/admin/api/releases")
def publish(body: Release, request: Request):
    actor, _ = current(request, csrf=True)
    if urlparse(body.url).netloc != request.url.netloc:
        raise AuthError(400, "invalid_download", "安装包须放在当前登录服务同域名 /downloads/ 下")
    auth = request.app.state.auth
    with auth.store.connect(write=True) as db:
        old = db.execute("SELECT payload FROM client_releases WHERE flavor=?", (body.flavor,)).fetchone()
        if old and tuple(map(int, body.version.split("."))) < tuple(map(int, json.loads(old["payload"])["version"].split("."))):
            raise AuthError(400, "version_regression", "不能发布更低版本；需要修复时请增加版本号")
        previous = json.loads(old["payload"]) if old else {}
        if (body.mandatory or previous.get("enforce_build")) and not body.build_sha256:
            raise AuthError(400, "build_required", "请导入新版打包生成的 release.json，缺少构建校验码")
        known = db.execute("SELECT build_sha256 FROM client_builds WHERE flavor=? AND version=?",
                           (body.flavor, body.version)).fetchone()
        if known and known["build_sha256"] != body.build_sha256:
            raise AuthError(400, "build_conflict", "同一版本不能替换构建校验码，请增加版本号重新打包")
        if body.build_sha256:
            db.execute("INSERT OR IGNORE INTO client_builds VALUES(?,?,?)",
                       (body.flavor, body.version, body.build_sha256))
        payload = body.model_dump()
        payload["minimum_version"] = body.version if body.mandatory else previous.get("minimum_version", "0.0.0.0")
        payload["enforce_build"] = bool(body.build_sha256 or previous.get("enforce_build"))
        db.execute("INSERT INTO client_releases(flavor,payload) VALUES(?,?) "
                   "ON CONFLICT(flavor) DO UPDATE SET payload=excluded.payload",
                   (body.flavor, json.dumps(payload, ensure_ascii=False)))
    record(request, actor, "publish_client", body.flavor + ":" + body.version)
    return {"ok": True}


@router.get("/api/v1/updates/latest")
def latest(request: Request, flavor: str = "all"):
    if flavor not in ("all", "v34", "v61"):
        raise AuthError(400, "invalid_flavor", "未知客户端体系")
    with request.app.state.auth.store.connect() as db:
        row = db.execute("SELECT payload FROM client_releases WHERE flavor=?", (flavor,)).fetchone()
    release = json.loads(row["payload"]) if row else None
    return {"release": release, "policy_protocol": 1, "client": client_status(request, flavor)}


def client_status(request, flavor=None):
    """Only our service can enforce this rule; caller-reported hashes are spoofable."""
    flavor = flavor or request.headers.get("x-studio-flavor", "")
    v, digest = request.headers.get("x-studio-version", ""), request.headers.get("x-studio-build", "")
    with request.app.state.auth.store.connect() as db:
        rows = db.execute("SELECT flavor,payload FROM client_releases").fetchall()
        releases = {r["flavor"]: json.loads(r["payload"]) for r in rows}
        current_release = releases.get(flavor)
        any_enforced = any(r.get("enforce_build") for r in releases.values())
        if flavor not in ("all", "v34", "v61") or request.headers.get("x-studio-flavor") != flavor:
            return {"allowed": not any_enforced and not v, "code": "client_unregistered",
                    "message": "客户端缺少版本登记，请安装最新 Studio"}
        if not current_release or not current_release.get("enforce_build"):
            # Once any distribution is enforced, an unknown flavor cannot bypass it.
            return {"allowed": not any_enforced and not v, "code": "client_unregistered",
                    "message": "此客户端体系尚未登记，请管理员发布对应安装包"}
        if not re.fullmatch(r"\d{1,5}(?:\.\d{1,5}){3}", v):
            return {"allowed": False, "code": "client_unregistered", "message": "客户端版本无效，请安装最新 Studio"}
        minimum = current_release.get("minimum_version", "0.0.0.0")
        if tuple(map(int, v.split("."))) < tuple(map(int, minimum.split("."))):
            return {"allowed": False, "code": "update_required",
                    "message": f"当前版本已停用，必须升级至 {minimum} 或更高版本"}
        known = db.execute("SELECT build_sha256 FROM client_builds WHERE flavor=? AND version=?", (flavor, v)).fetchone()
        if not known or known["build_sha256"] != digest:
            return {"allowed": False, "code": "client_unregistered",
                    "message": "客户端版本或发布校验码未通过，请安装后台发布的正式版本"}
    return {"allowed": True, "code": "ok", "message": "客户端版本和发布校验码已确认"}


def require_client(request):
    result = client_status(request)
    if not result["allowed"]:
        raise AuthError(426, result["code"], result["message"])
