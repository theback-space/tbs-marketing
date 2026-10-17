"""Facebook Page and Instagram Business adapters (Meta Graph API).

Permissions needed on the Page access token: pages_manage_posts, pages_read_engagement,
pages_manage_engagement, pages_read_user_content, instagram_basic,
instagram_content_publish, instagram_manage_comments.
"""

from __future__ import annotations

from typing import Any

import httpx2 as httpx

from tbs_social.platforms.base import Inbound, Metrics, Platform, PlatformError

RECENT_POSTS = 10


class _Graph:
    def __init__(self, token: str, version: str, client: httpx.Client | None = None):
        if not token:
            raise PlatformError("META_PAGE_ACCESS_TOKEN is not set")
        self.token = token
        self.base = f"https://graph.facebook.com/{version}"
        self.http = client or httpx.Client(timeout=30)

    def _call(self, method: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = {**(params or {}), "access_token": self.token}
        if method == "GET":
            resp = self.http.get(f"{self.base}/{path}", params=params)
        else:
            resp = self.http.post(f"{self.base}/{path}", data=params)
        payload = resp.json() if resp.content else {}
        if resp.status_code >= 400 or "error" in payload:
            message = payload.get("error", {}).get("message", resp.text[:300])
            raise PlatformError(f"Graph API {method} {path}: {message}")
        return payload

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        return self._call("GET", path, params)

    def post(self, path: str, **params: Any) -> dict[str, Any]:
        return self._call("POST", path, params)


class FacebookPage(Platform):
    name = "facebook"
    can_like_comments = True

    def __init__(self, page_id: str, token: str, version: str, client: httpx.Client | None = None):
        if not page_id:
            raise PlatformError("META_PAGE_ID is not set")
        self.page_id = page_id
        self.graph = _Graph(token, version, client)

    def publish(self, body: str, image_url: str | None = None) -> str:
        if image_url:
            result = self.graph.post(f"{self.page_id}/photos", url=image_url, caption=body)
            return result.get("post_id") or result["id"]
        return self.graph.post(f"{self.page_id}/feed", message=body)["id"]

    def _recent_post_ids(self) -> list[str]:
        data = self.graph.get(f"{self.page_id}/posts", fields="id", limit=RECENT_POSTS)
        return [p["id"] for p in data.get("data", [])]

    def fetch_inbound(self) -> list[Inbound]:
        items: list[Inbound] = []
        for post_id in self._recent_post_ids():
            data = self.graph.get(f"{post_id}/comments", fields="id,from,message,created_time",
                                  filter="stream", order="reverse_chronological", limit=50)
            for c in data.get("data", []):
                author = (c.get("from") or {}).get("name")
                if (c.get("from") or {}).get("id") == self.page_id:
                    continue  # our own replies
                items.append(Inbound(external_id=c["id"], kind="comment", text=c.get("message", ""),
                                     author=author, post_external_id=post_id,
                                     received_at=c.get("created_time", "")))
        return items

    def fetch_metrics(self) -> Metrics:
        page = self.graph.get(self.page_id, fields="followers_count,fan_count")
        metrics = Metrics(account={"followers": page.get("followers_count", 0),
                                   "page_likes": page.get("fan_count", 0)})
        for post_id in self._recent_post_ids():
            p = self.graph.get(post_id, fields="shares,reactions.summary(total_count).limit(0),"
                                               "comments.summary(total_count).limit(0)")
            metrics.posts[post_id] = {
                "reactions": p.get("reactions", {}).get("summary", {}).get("total_count", 0),
                "comments": p.get("comments", {}).get("summary", {}).get("total_count", 0),
                "shares": p.get("shares", {}).get("count", 0),
            }
        return metrics

    def reply(self, inbound_external_id: str, text: str) -> str:
        return self.graph.post(f"{inbound_external_id}/comments", message=text)["id"]

    def like_comment(self, inbound_external_id: str) -> None:
        self.graph.post(f"{inbound_external_id}/likes")


class InstagramBusiness(Platform):
    name = "instagram"
    requires_image = True  # the IG API cannot publish text-only posts

    def __init__(self, ig_user_id: str, token: str, version: str, client: httpx.Client | None = None):
        if not ig_user_id:
            raise PlatformError("META_IG_USER_ID is not set")
        self.ig_user_id = ig_user_id
        self.graph = _Graph(token, version, client)

    def publish(self, body: str, image_url: str | None = None) -> str:
        if not image_url:
            raise PlatformError("Instagram posts need an image_url")
        container = self.graph.post(f"{self.ig_user_id}/media", image_url=image_url, caption=body)
        return self.graph.post(f"{self.ig_user_id}/media_publish", creation_id=container["id"])["id"]

    def _recent_media(self) -> list[dict[str, Any]]:
        return self.graph.get(f"{self.ig_user_id}/media", fields="id,like_count,comments_count",
                              limit=RECENT_POSTS).get("data", [])

    def fetch_inbound(self) -> list[Inbound]:
        items: list[Inbound] = []
        for media in self._recent_media():
            data = self.graph.get(f"{media['id']}/comments", fields="id,text,username,timestamp", limit=50)
            for c in data.get("data", []):
                items.append(Inbound(external_id=c["id"], kind="comment", text=c.get("text", ""),
                                     author=c.get("username"), post_external_id=media["id"],
                                     received_at=c.get("timestamp", "")))
        return items

    def fetch_metrics(self) -> Metrics:
        acct = self.graph.get(self.ig_user_id, fields="followers_count,follows_count,media_count")
        metrics = Metrics(account={"followers": acct.get("followers_count", 0),
                                   "following": acct.get("follows_count", 0),
                                   "posts": acct.get("media_count", 0)})
        for media in self._recent_media():
            metrics.posts[media["id"]] = {"likes": media.get("like_count", 0),
                                          "comments": media.get("comments_count", 0)}
        return metrics

    def reply(self, inbound_external_id: str, text: str) -> str:
        return self.graph.post(f"{inbound_external_id}/replies", message=text)["id"]
