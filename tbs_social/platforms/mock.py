"""Offline platform for demos and tests — no credentials, no network."""

from __future__ import annotations

import itertools
import random
from datetime import datetime, timezone

from tbs_social.platforms.base import Inbound, Metrics, Platform

SAMPLE_COMMENTS = [
    ("sam_lifts", "Love these desk stretch tips, my lower back thanks you!"),
    ("officeworker22", "What chair height do you recommend for someone 5'4\"?"),
    ("jen.k", "I was diagnosed with sciatica last month, will this help? Call me on 555-201-3344"),
    ("deals4u", "Get 10k followers fast!!! DM us"),
    ("mike_r", "Waited 40 minutes past my appointment time today. Not happy."),
    ("runner_amy", "Do you have any weekend availability?"),
    ("posture_nerd", "Great myth-busting post, sharing with my team."),
]


class MockPlatform(Platform):
    name = "mock"
    can_like_comments = True

    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)
        self._ids = itertools.count(1)
        self.posts: dict[str, str] = {"seed-post": "Welcome to our page!"}
        self.replies: list[tuple[str, str]] = []
        self.likes: list[str] = []
        self._followers = 480
        self._served = 0

    def publish(self, body: str, image_url: str | None = None) -> str:
        post_id = f"mock-post-{next(self._ids)}"
        self.posts[post_id] = body
        return post_id

    def fetch_inbound(self) -> list[Inbound]:
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        batch = SAMPLE_COMMENTS[self._served:self._served + 3] or []
        self._served += len(batch)
        post_ids = list(self.posts)
        return [
            Inbound(external_id=f"mock-c-{self._served - len(batch) + i}", kind="comment", text=text,
                    author=author, post_external_id=self.rng.choice(post_ids), received_at=stamp)
            for i, (author, text) in enumerate(batch)
        ]

    def fetch_metrics(self) -> Metrics:
        self._followers += self.rng.randint(0, 6)
        posts = {
            pid: {"reactions": self.rng.randint(5, 80), "comments": self.rng.randint(0, 12),
                  "shares": self.rng.randint(0, 9), "reach": self.rng.randint(200, 3000)}
            for pid in self.posts
        }
        return Metrics(account={"followers": self._followers, "posts": len(self.posts)}, posts=posts)

    def reply(self, inbound_external_id: str, text: str) -> str:
        self.replies.append((inbound_external_id, text))
        return f"mock-reply-{next(self._ids)}"

    def like_comment(self, inbound_external_id: str) -> None:
        self.likes.append(inbound_external_id)
