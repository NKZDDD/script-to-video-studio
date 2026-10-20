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
        db.execute("INSERT INTO client_releases(flavor,payload) VALUES(?,?) "
                   "ON CONFLICT(flavor) DO UPDATE SET payload=excluded.payload",
                   (body.flavor, json.dumps(body.model_dump(), ensure_ascii=False)))
    record(request, actor, "publish_client", body.flavor + ":" + body.version)
    return {"ok": True}


@router.get("/api/v1/updates/latest")
def latest(request: Request, flavor: str = "all"):
    if flavor not in ("all", "v34", "v61"):
        raise AuthError(400, "invalid_flavor", "未知客户端体系")
    with request.app.state.auth.store.connect() as db:
        row = db.execute("SELECT payload FROM client_releases WHERE flavor=?", (flavor,)).fetchone()
    return {"release": json.loads(row["payload"]) if row else None}
