"""Platform adapter interface. Each network implements what its official API allows."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Inbound:
    external_id: str
    kind: str                 # comment | mention | message
    text: str
    author: str | None
    post_external_id: str | None
    received_at: str


@dataclass
class Metrics:
    account: dict[str, float] = field(default_factory=dict)
    posts: dict[str, dict[str, float]] = field(default_factory=dict)  # post id -> metrics


class PlatformError(RuntimeError):
    pass


class Platform(ABC):
    name: str
    # What the official API lets a business account do. The engine checks these
    # rather than trying (and failing) actions a network does not permit.
    can_like_comments: bool = False
    can_follow: bool = False
    requires_image: bool = False

    @abstractmethod
    def publish(self, body: str, image_url: str | None = None) -> str:
        """Publish a post and return its external id."""

    @abstractmethod
    def fetch_inbound(self) -> list[Inbound]:
        """Recent comments/mentions on our content."""

    @abstractmethod
    def fetch_metrics(self) -> Metrics: ...

    @abstractmethod
    def reply(self, inbound_external_id: str, text: str) -> str: ...

    def like_comment(self, inbound_external_id: str) -> None:
        raise PlatformError(f"{self.name} does not allow liking comments via its API")
