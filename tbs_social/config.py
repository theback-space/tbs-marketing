"""Runtime settings (from environment) and the brand profile (from TOML)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Brand:
    name: str
    website: str = ""
    contact_email: str = ""
    industry: str = "Healthcare"
    voice: str = ""
    audience: str = ""
    services: list[str] = field(default_factory=list)
    content_pillars: list[str] = field(default_factory=list)
    hashtags: list[str] = field(default_factory=list)
    disclaimer: str = ""
    private_channel: str = ""
    banned_phrases: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "Brand":
        with open(path, "rb") as f:
            data = tomllib.load(f)
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def as_prompt(self) -> str:
        def bullets(items: list[str]) -> str:
            return "\n".join(f"- {i}" for i in items) or "- (none)"

        return (
            f"Business: {self.name} ({self.website})\n"
            f"Industry: {self.industry}\n"
            f"Audience: {self.audience}\n"
            f"Voice:\n{self.voice.strip()}\n"
            f"Services:\n{bullets(self.services)}\n"
            f"Content pillars:\n{bullets(self.content_pillars)}\n"
            f"Preferred hashtags: {' '.join(self.hashtags)}\n"
            f"Health-guidance disclaimer: {self.disclaimer}\n"
            f"Private channel for personal health matters: {self.private_channel}\n"
            f"Never use these phrases: {', '.join(self.banned_phrases)}"
        )


@dataclass(frozen=True)
class Settings:
    db_path: str = "tbs_social.db"
    brand_file: str = "brand.toml"
    platforms: tuple[str, ...] = ("mock",)
    automation: str = "approve"  # "approve" | "assist"
    max_auto_likes: int = 20
    max_auto_replies: int = 10
    meta_page_id: str = ""
    meta_page_token: str = ""
    meta_ig_user_id: str = ""
    meta_graph_version: str = "v21.0"
    dashboard_user: str = "admin"
    dashboard_password: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv()
        env = os.environ.get
        automation = env("TBS_AUTOMATION", "approve").strip().lower()
        if automation not in ("approve", "assist"):
            raise ValueError("TBS_AUTOMATION must be 'approve' or 'assist'")
        return cls(
            db_path=env("TBS_DB_PATH", "tbs_social.db"),
            brand_file=env("TBS_BRAND_FILE", "brand.toml"),
            platforms=tuple(
                p.strip().lower() for p in env("TBS_PLATFORMS", "mock").split(",") if p.strip()
            ),
            automation=automation,
            max_auto_likes=int(env("TBS_MAX_AUTO_LIKES", "20")),
            max_auto_replies=int(env("TBS_MAX_AUTO_REPLIES", "10")),
            meta_page_id=env("META_PAGE_ID", ""),
            meta_page_token=env("META_PAGE_ACCESS_TOKEN", ""),
            meta_ig_user_id=env("META_IG_USER_ID", ""),
            meta_graph_version=env("META_GRAPH_VERSION", "v21.0"),
            dashboard_user=env("TBS_DASHBOARD_USER", "admin"),
            dashboard_password=env("TBS_DASHBOARD_PASSWORD", ""),
        )


def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader; real environment variables take precedence."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
