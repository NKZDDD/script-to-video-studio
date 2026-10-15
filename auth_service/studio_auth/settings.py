from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class Settings:
    database: Path
    access_seconds: int = 900
    session_seconds: int = 30 * 86400
    require_https: bool = True
    login_ip_limit: int = 30
    login_user_limit: int = 10
    rate_window: int = 300

    def __post_init__(self):
        if not 60 <= self.access_seconds <= 3600:
            raise ValueError("Access lifetime must be 60..3600 seconds")
        if not self.access_seconds <= self.session_seconds <= 90 * 86400:
            raise ValueError("Session lifetime must be >= access lifetime and <= 90 days")
        if min(self.login_ip_limit, self.login_user_limit, self.rate_window) < 1:
            raise ValueError("Rate limits must be positive")

    @classmethod
    def from_env(cls):
        return cls(
            database=Path(os.environ.get("STUDIO_AUTH_DB", "data/auth.sqlite3")).resolve(),
            access_seconds=int(os.environ.get("STUDIO_AUTH_ACCESS_SECONDS", "900")),
            session_seconds=int(os.environ.get("STUDIO_AUTH_SESSION_SECONDS", "2592000")),
            require_https=os.environ.get("STUDIO_AUTH_REQUIRE_HTTPS", "1") != "0",
        )
