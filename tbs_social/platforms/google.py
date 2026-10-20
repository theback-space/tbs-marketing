"""Google Business Profile adapter: reviews, local posts, and performance metrics.

Auth is OAuth 2.0 with a long-lived refresh token for a Google account that manages the
listing (scope: https://www.googleapis.com/auth/business.manage). Google must approve the
Cloud project for Business Profile API access before any of these calls succeed.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any

import httpx2 as httpx

from tbs_social.platforms.base import Inbound, Metrics, Platform, PlatformError

TOKEN_URL = "https://oauth2.googleapis.com/token"
V4 = "https://mybusiness.googleapis.com/v4"
PERFORMANCE = "https://businessprofileperformance.googleapis.com/v1"
ACCOUNTS = "https://mybusinessaccountmanagement.googleapis.com/v1"
BUSINESS_INFO = "https://mybusinessbusinessinformation.googleapis.com/v1"

STARS = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}
DAILY_METRICS = {
    "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH": "search_views",
    "BUSINESS_IMPRESSIONS_MOBILE_SEARCH": "search_views",
    "BUSINESS_IMPRESSIONS_DESKTOP_MAPS": "maps_views",
    "BUSINESS_IMPRESSIONS_MOBILE_MAPS": "maps_views",
    "CALL_CLICKS": "calls",
    "WEBSITE_CLICKS": "website_clicks",
    "BUSINESS_DIRECTION_REQUESTS": "direction_requests",
    "BUSINESS_BOOKINGS": "bookings",
}
METRICS_WINDOW_DAYS = 28
POST_SUMMARY_LIMIT = 1500


class _GoogleAuth:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str, http: httpx.Client):
        if not (client_id and client_secret and refresh_token):
            raise PlatformError("GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN must be set")
        self.client_id, self.client_secret, self.refresh_token = client_id, client_secret, refresh_token
        self.http = http
        self._token = ""
        self._expires = 0.0

    def token(self) -> str:
        if time.time() < self._expires - 60:
            return self._token
        resp = self.http.post(TOKEN_URL, data={
            "client_id": self.client_id, "client_secret": self.client_secret,
            "refresh_token": self.refresh_token, "grant_type": "refresh_token",
        })
        payload = resp.json() if resp.content else {}
        if resp.status_code >= 400 or "access_token" not in payload:
            raise PlatformError(f"Google token refresh failed: {payload.get('error_description') or resp.text[:200]}")
        self._token = payload["access_token"]
        self._expires = time.time() + int(payload.get("expires_in", 3600))
        return self._token


class _GoogleAPI:
    def __init__(self, auth: _GoogleAuth, http: httpx.Client):
        self.auth, self.http = auth, http

    def request(self, method: str, url: str, *, params: dict[str, Any] | list | None = None,
                json: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = self.http.request(method, url, params=params, json=json,
                                 headers={"Authorization": f"Bearer {self.auth.token()}"})
        payload = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            message = (payload.get("error") or {}).get("message") if isinstance(payload, dict) else None
            raise PlatformError(f"Google API {method} {url.split('?')[0]}: {message or resp.text[:300]}")
        return payload


class GoogleBusinessProfile(Platform):
    name = "google"
    can_like_comments = False  # reviews can be answered, not liked

    def __init__(self, account_id: str, location_id: str, client_id: str, client_secret: str,
                 refresh_token: str, client: httpx.Client | None = None):
        if not (account_id and location_id):
            raise PlatformError("GBP_ACCOUNT_ID and GBP_LOCATION_ID must be set "
                                "(run `python -m tbs_social google-locations` to find them)")
        http = client or httpx.Client(timeout=30)
        self.api = _GoogleAPI(_GoogleAuth(client_id, client_secret, refresh_token, http), http)
        self.account_id = account_id.removeprefix("accounts/")
        self.location_id = location_id.removeprefix("locations/")
        self.location_path = f"accounts/{self.account_id}/locations/{self.location_id}"

    def publish(self, body: str, image_url: str | None = None) -> str:
        if len(body) > POST_SUMMARY_LIMIT:
            raise PlatformError(f"Google posts are limited to {POST_SUMMARY_LIMIT} characters")
        post: dict[str, Any] = {"languageCode": "en-US", "summary": body, "topicType": "STANDARD"}
        if image_url:
            post["media"] = [{"mediaFormat": "PHOTO", "sourceUrl": image_url}]
        return self.api.request("POST", f"{V4}/{self.location_path}/localPosts", json=post)["name"]

    def _reviews(self) -> dict[str, Any]:
        return self.api.request("GET", f"{V4}/{self.location_path}/reviews",
                                params={"pageSize": 50, "orderBy": "updateTime desc"})

    def fetch_inbound(self) -> list[Inbound]:
        items = []
        for r in self._reviews().get("reviews", []):
            if r.get("reviewReply"):
                continue  # already answered (here or directly on Google)
            stars = STARS.get(r.get("starRating", ""), 0)
            comment = (r.get("comment") or "").strip() or "(no written review)"
            items.append(Inbound(
                external_id=r["name"], kind="review", text=f"[{stars}-star review] {comment}",
                author=(r.get("reviewer") or {}).get("displayName"), post_external_id=None,
                received_at=r.get("updateTime") or r.get("createTime", "")))
        return items

    def reply(self, inbound_external_id: str, text: str) -> str:
        self.api.request("PUT", f"{V4}/{inbound_external_id}/reply", json={"comment": text})
        return f"{inbound_external_id}/reply"

    def fetch_metrics(self) -> Metrics:
        reviews = self._reviews()
        account: dict[str, float] = {
            "average_rating": round(float(reviews.get("averageRating", 0)), 2),
            "total_reviews": int(reviews.get("totalReviewCount", 0)),
        }
        end = date.today() - timedelta(days=1)  # today's numbers are incomplete
        start = end - timedelta(days=METRICS_WINDOW_DAYS - 1)
        params: list[tuple[str, Any]] = [("dailyMetrics", m) for m in DAILY_METRICS]
        for prefix, d in (("dailyRange.startDate", start), ("dailyRange.endDate", end)):
            params += [(f"{prefix}.year", d.year), (f"{prefix}.month", d.month), (f"{prefix}.day", d.day)]
        series = self.api.request(
            "GET", f"{PERFORMANCE}/locations/{self.location_id}:fetchMultiDailyMetricsTimeSeries", params=params)
        totals = {label: 0 for label in DAILY_METRICS.values()}
        for group in series.get("multiDailyMetricTimeSeries", []):
            for s in group.get("dailyMetricTimeSeries", []):
                label = DAILY_METRICS.get(s.get("dailyMetric"))
                if label is None:
                    continue
                for point in (s.get("timeSeries") or {}).get("datedValues", []):
                    totals[label] += int(point.get("value", 0))  # zero days omit "value"
        account.update({f"{k}_{METRICS_WINDOW_DAYS}d": v for k, v in totals.items()})
        return Metrics(account=account)


def list_locations(client_id: str, client_secret: str, refresh_token: str,
                   client: httpx.Client | None = None) -> list[tuple[str, str, str]]:
    """Return (account id, location id, business name) for every listing the account manages."""
    http = client or httpx.Client(timeout=30)
    api = _GoogleAPI(_GoogleAuth(client_id, client_secret, refresh_token, http), http)
    found = []
    for account in api.request("GET", f"{ACCOUNTS}/accounts").get("accounts", []):
        locations = api.request("GET", f"{BUSINESS_INFO}/{account['name']}/locations",
                                params={"readMask": "name,title", "pageSize": 100})
        for loc in locations.get("locations", []):
            found.append((account["name"].removeprefix("accounts/"),
                          loc["name"].removeprefix("locations/"), loc.get("title", "")))
    return found
