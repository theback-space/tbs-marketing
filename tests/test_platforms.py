"""Adapter tests against fake Google and Meta servers (no network)."""

import json

import httpx2 as httpx
import pytest

from tbs_social.platforms.base import PlatformError
from tbs_social.platforms.google import GoogleBusinessProfile, list_locations
from tbs_social.platforms.meta import FacebookPage, InstagramBusiness


class FakeServer:
    def __init__(self, routes):
        self.routes = routes  # {(method, path suffix): response json or callable(request)}
        self.calls = []

    def __call__(self, request):
        self.calls.append(request)
        for (method, suffix), resp in self.routes.items():
            if request.method == method and request.url.path.endswith(suffix):
                body = resp(request) if callable(resp) else resp
                status = body.pop("_status", 200) if isinstance(body, dict) else 200
                return httpx.Response(status, json=body)
        return httpx.Response(404, json={"error": {"message": f"no route {request.method} {request.url.path}"}})

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self))


TOKEN = {("POST", "/token"): {"access_token": "at-1", "expires_in": 3600}}
LOC = "accounts/111/locations/222"


def gbp(routes):
    server = FakeServer({**TOKEN, **routes})
    return GoogleBusinessProfile("111", "222", "cid", "secret", "refresh", client=server.client()), server


REVIEWS = {
    "averageRating": 4.666, "totalReviewCount": 3,
    "reviews": [
        {"name": f"{LOC}/reviews/r1", "starRating": "FIVE", "comment": "Fixed my posture!",
         "reviewer": {"displayName": "Ana"}, "updateTime": "2026-10-10T10:00:00Z"},
        {"name": f"{LOC}/reviews/r2", "starRating": "FOUR", "reviewer": {"displayName": "Ben"},
         "updateTime": "2026-10-09T10:00:00Z"},
        {"name": f"{LOC}/reviews/r3", "starRating": "FIVE", "comment": "Great",
         "reviewReply": {"comment": "Thanks!"}},
    ],
}


def test_google_reviews_skip_answered_and_include_rating():
    platform, server = gbp({("GET", "/reviews"): REVIEWS})
    items = platform.fetch_inbound()
    assert [i.external_id for i in items] == [f"{LOC}/reviews/r1", f"{LOC}/reviews/r2"]
    assert items[0].text == "[5-star review] Fixed my posture!" and items[0].kind == "review"
    assert items[1].text == "[4-star review] (no written review)"
    assert server.calls[-1].headers["Authorization"] == "Bearer at-1"


def test_google_token_is_reused_until_expiry():
    platform, server = gbp({("GET", "/reviews"): REVIEWS})
    platform.fetch_inbound()
    platform.fetch_inbound()
    assert sum(1 for c in server.calls if c.url.path.endswith("/token")) == 1


def test_google_reply_puts_to_review():
    platform, server = gbp({("PUT", "/reply"): {"comment": "Thank you!"}})
    platform.reply(f"{LOC}/reviews/r1", "Thank you!")
    call = server.calls[-1]
    assert call.url.path == f"/v4/{LOC}/reviews/r1/reply"
    assert json.loads(call.content) == {"comment": "Thank you!"}


def test_google_publish_local_post_with_photo():
    platform, server = gbp({("POST", "/localPosts"): {"name": f"{LOC}/localPosts/p1"}})
    assert platform.publish("Open Saturdays!", "https://img/x.jpg") == f"{LOC}/localPosts/p1"
    body = json.loads(server.calls[-1].content)
    assert body["summary"] == "Open Saturdays!" and body["topicType"] == "STANDARD"
    assert body["media"] == [{"mediaFormat": "PHOTO", "sourceUrl": "https://img/x.jpg"}]
    with pytest.raises(PlatformError):
        platform.publish("x" * 1501)


def test_google_metrics_sum_daily_series():
    series = {"multiDailyMetricTimeSeries": [{"dailyMetricTimeSeries": [
        {"dailyMetric": "CALL_CLICKS", "timeSeries": {"datedValues": [{"value": "3"}, {"date": {}}]}},
        {"dailyMetric": "BUSINESS_IMPRESSIONS_MOBILE_SEARCH", "timeSeries": {"datedValues": [{"value": "40"}]}},
        {"dailyMetric": "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH", "timeSeries": {"datedValues": [{"value": "10"}]}},
    ]}]}
    platform, server = gbp({("GET", "/reviews"): REVIEWS, ("GET", ":fetchMultiDailyMetricsTimeSeries"): series})
    account = platform.fetch_metrics().account
    assert account["average_rating"] == 4.67 and account["total_reviews"] == 3
    assert account["calls_28d"] == 3 and account["search_views_28d"] == 50 and account["bookings_28d"] == 0
    perf = server.calls[-1]
    assert perf.url.path == "/v1/locations/222:fetchMultiDailyMetricsTimeSeries"
    assert "dailyRange.startDate.year" in perf.url.params


def test_google_errors_surface_as_platform_errors():
    platform, _ = gbp({("GET", "/reviews"): {"_status": 403, "error": {"message": "API not enabled"}}})
    with pytest.raises(PlatformError, match="API not enabled"):
        platform.fetch_inbound()
    bad_token = FakeServer({("POST", "/token"): {"_status": 400, "error_description": "Token revoked"}})
    p = GoogleBusinessProfile("1", "2", "c", "s", "r", client=bad_token.client())
    with pytest.raises(PlatformError, match="Token revoked"):
        p.fetch_inbound()


def test_google_list_locations():
    server = FakeServer({**TOKEN,
                         ("GET", "/v1/accounts"): {"accounts": [{"name": "accounts/111"}]},
                         ("GET", "/accounts/111/locations"): {"locations": [{"name": "locations/222",
                                                                              "title": "The Back Space"}]}})
    assert list_locations("c", "s", "r", client=server.client()) == [("111", "222", "The Back Space")]


def test_facebook_comments_replies_and_likes():
    server = FakeServer({
        ("GET", "/PAGE/posts"): {"data": [{"id": "PAGE_1"}]},
        ("GET", "/PAGE_1/comments"): {"data": [
            {"id": "c1", "message": "Love it", "from": {"id": "u1", "name": "Ana"}, "created_time": "t"},
            {"id": "c2", "message": "Thanks Ana", "from": {"id": "PAGE", "name": "Us"}, "created_time": "t"},
        ]},
        ("POST", "/c1/comments"): {"id": "r1"},
        ("POST", "/c1/likes"): {"success": True},
        ("POST", "/PAGE/feed"): {"id": "PAGE_2"},
    })
    fb = FacebookPage("PAGE", "tok", "v21.0", client=server.client())
    items = fb.fetch_inbound()
    assert [(i.external_id, i.author) for i in items] == [("c1", "Ana")]  # our own reply skipped
    assert fb.reply("c1", "Thank you!") == "r1"
    fb.like_comment("c1")
    assert fb.publish("Hello") == "PAGE_2"
    assert all(c.url.params.get("access_token") == "tok" or b"access_token=tok" in c.content
               for c in server.calls)


def test_instagram_requires_image_and_publishes_in_two_steps():
    server = FakeServer({("POST", "/IG/media"): {"id": "container"},
                         ("POST", "/IG/media_publish"): {"id": "media1"}})
    ig = InstagramBusiness("IG", "tok", "v21.0", client=server.client())
    with pytest.raises(PlatformError):
        ig.publish("caption only")
    assert ig.publish("caption", "https://img/x.jpg") == "media1"
    assert b"creation_id=container" in server.calls[-1].content
