"""The monitoring cycle: publish what's due, pull new comments, triage, engage, record metrics."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from tbs_social import compliance
from tbs_social.ai import ContentAI
from tbs_social.config import Brand, Settings
from tbs_social.db import DB, now
from tbs_social.platforms.base import Platform, PlatformError

log = logging.getLogger(__name__)

# Intents we will ever answer without a human, and only in "assist" mode.
AUTO_REPLY_INTENTS = {"praise"}


class ComplianceBlocked(ValueError):
    pass


@dataclass
class CycleReport:
    published: int = 0
    new_interactions: int = 0
    flagged: int = 0
    auto_likes: int = 0
    auto_replies: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        text = (f"published {self.published}, new comments {self.new_interactions}, "
                f"flagged for staff {self.flagged}, auto-likes {self.auto_likes}, "
                f"auto-replies {self.auto_replies}")
        return text + (f", errors {len(self.errors)}" if self.errors else "")


class Engine:
    def __init__(self, db: DB, platforms: dict[str, Platform], ai: ContentAI, brand: Brand, settings: Settings):
        self.db, self.platforms, self.ai, self.brand, self.settings = db, platforms, ai, brand, settings

    # --- content ---------------------------------------------------------
    def generate_drafts(self, platform: str, count: int = 3, topic: str | None = None) -> list[int]:
        ids = []
        for draft in self.ai.generate_posts(platform, count, topic):
            issues = compliance.check_outbound(draft.body, self.brand, is_reply=False,
                                               hipaa=self.settings.hipaa)
            ids.append(self.db.add_post(platform, draft.body, pillar=draft.pillar,
                                        image_idea=draft.image_idea, compliance_notes=issues))
        return ids

    def approve_post(self, post_id: int, *, body: str | None = None, image_url: str | None = None,
                     scheduled_for: str | None = None) -> None:
        post = self.db.get_post(post_id)
        if post is None:
            raise KeyError(post_id)
        body = body if body is not None else post["body"]
        issues = compliance.check_outbound(body, self.brand, is_reply=False, hipaa=self.settings.hipaa)
        blocking = [i for i in issues if "banned phrase" in i]
        if blocking:
            raise ComplianceBlocked("; ".join(blocking))
        self.db.update_post(post_id, body=body, image_url=image_url or post["image_url"],
                            scheduled_for=scheduled_for or None, status="approved",
                            compliance_notes=json.dumps(issues))

    def mark_posted_manually(self, post_id: int, body: str) -> None:
        """Record a draft that staff copied and posted themselves on an unconnected platform."""
        post = self.db.get_post(post_id)
        if post is None:
            raise KeyError(post_id)
        issues = compliance.check_outbound(body, self.brand, is_reply=False, hipaa=self.settings.hipaa)
        blocking = [i for i in issues if "banned phrase" in i]
        if blocking:
            raise ComplianceBlocked("; ".join(blocking))
        self.db.update_post(post_id, body=body, status="published", published_at=now(),
                            compliance_notes=json.dumps(issues))
        self.db.log_action(post["platform"], "publish", str(post_id), detail=body[:120],
                           automated=False, outcome="manual")

    def publish_due(self, report: CycleReport) -> None:
        for post in self.db.due_posts(now()):
            platform = self.platforms.get(post["platform"])
            if platform is None:
                continue
            try:
                external_id = platform.publish(post["body"], post["image_url"])
            except PlatformError as e:
                self.db.update_post(post["id"], status="failed", error=str(e))
                self.db.log_action(post["platform"], "publish", str(post["id"]), detail=str(e),
                                   automated=True, outcome="error")
                report.errors.append(f"publish #{post['id']}: {e}")
                continue
            self.db.update_post(post["id"], status="published", external_id=external_id,
                                published_at=now(), error=None)
            self.db.log_action(post["platform"], "publish", external_id, detail=post["body"][:120],
                               automated=True, outcome="ok")
            report.published += 1

    # --- engagement ------------------------------------------------------
    def send_reply(self, interaction_id: int, text: str, *, automated: bool) -> None:
        row = self.db.get_interaction(interaction_id)
        if row is None:
            raise KeyError(interaction_id)
        issues = compliance.check_outbound(text, self.brand, is_reply=True, hipaa=self.settings.hipaa)
        if issues:
            raise ComplianceBlocked("; ".join(issues))
        platform = self.platforms[row["platform"]]
        try:
            reply_id = platform.reply(row["external_id"], text)
        except PlatformError as e:
            self.db.log_action(row["platform"], "reply", row["external_id"], detail=str(e),
                               automated=automated, outcome="error")
            raise
        self.db.update_interaction(interaction_id, status="replied", draft_reply=text)
        self.db.log_action(row["platform"], "reply", reply_id, detail=text, automated=automated, outcome="ok")

    def like(self, interaction_id: int, *, automated: bool) -> None:
        row = self.db.get_interaction(interaction_id)
        if row is None:
            raise KeyError(interaction_id)
        if row["phi_risk"]:
            raise ComplianceBlocked("liking a comment that contains health details can imply a patient relationship")
        platform = self.platforms[row["platform"]]
        try:
            platform.like_comment(row["external_id"])
        except PlatformError as e:
            self.db.log_action(row["platform"], "like", row["external_id"], detail=str(e),
                               automated=automated, outcome="error")
            raise
        self.db.update_interaction(interaction_id, liked=1)
        self.db.log_action(row["platform"], "like", row["external_id"], automated=automated, outcome="ok")

    def dismiss(self, interaction_id: int) -> None:
        self.db.update_interaction(interaction_id, status="dismissed")

    def process_inbound(self, name: str, platform: Platform, report: CycleReport) -> None:
        likes = replies = 0
        for item in platform.fetch_inbound():
            if not item.text.strip():
                continue
            interaction_id = self.db.upsert_interaction(
                name, item.external_id, item.kind, item.text, author=item.author,
                post_external_id=item.post_external_id, received_at=item.received_at or now())
            if interaction_id is None:
                continue  # already seen
            report.new_interactions += 1

            hipaa = self.settings.hipaa
            phi_signals = compliance.scan_inbound(item.text) if hipaa else []
            emergency = hipaa and compliance.is_emergency(item.text)
            try:
                t = self.ai.triage(name, item.text, item.author, phi_signals)
            except Exception as e:  # never let one comment stall the cycle
                log.warning("triage failed for %s: %s", item.external_id, e)
                self.db.update_interaction(interaction_id, phi_risk=int(bool(phi_signals)), needs_human=1,
                                           triage_reason=f"AI triage unavailable: {e}", status="drafted")
                report.flagged += 1
                continue

            reply = t.reply.strip()
            reply_issues = compliance.check_outbound(
                reply, self.brand, is_reply=True, hipaa=hipaa) if reply else []
            reasons = [t.reason]
            if phi_signals:
                reasons.append("PHI signals: " + ", ".join(phi_signals))
            if emergency:
                reasons.append("possible emergency — respond personally and promptly")
            if reply_issues:
                reasons.append("draft reply issues: " + "; ".join(reply_issues))
            needs_human = t.needs_human or bool(phi_signals) or emergency or bool(reply_issues)

            self.db.update_interaction(
                interaction_id, sentiment=t.sentiment, intent=t.intent, phi_risk=int(bool(phi_signals)),
                needs_human=int(needs_human), triage_reason=" | ".join(reasons), draft_reply=reply,
                status="dismissed" if t.intent == "spam" else "drafted")
            if needs_human:
                report.flagged += 1
            if self.settings.automation != "assist" or needs_human:
                continue

            # Assist mode: only low-risk, positive engagement goes out on its own.
            if (t.like and t.sentiment == "positive" and platform.can_like_comments
                    and likes < self.settings.max_auto_likes):
                try:
                    self.like(interaction_id, automated=True)
                    likes += 1
                except (PlatformError, ComplianceBlocked) as e:
                    report.errors.append(f"like {item.external_id}: {e}")
            if reply and t.intent in AUTO_REPLY_INTENTS and replies < self.settings.max_auto_replies:
                try:
                    self.send_reply(interaction_id, reply, automated=True)
                    replies += 1
                except (PlatformError, ComplianceBlocked) as e:
                    report.errors.append(f"reply {item.external_id}: {e}")
        report.auto_likes += likes
        report.auto_replies += replies

    # --- metrics ---------------------------------------------------------
    def record_metrics(self, name: str, platform: Platform) -> None:
        m = platform.fetch_metrics()
        self.db.add_metrics(name, "account", "account", m.account)
        for post_id, data in m.posts.items():
            self.db.add_metrics(name, "post", post_id, data)

    def performance_report(self) -> str:
        lines = []
        for row in self.db.latest_metrics("account"):
            lines.append(f"{row['platform']} account: {row['data']}")
        posts = {p["external_id"]: p for p in self.db.list_posts("published") if p["external_id"]}
        for row in self.db.latest_metrics("post"):
            body = posts.get(row["external_id"])
            label = (body["body"][:80] + "…") if body else row["external_id"]
            lines.append(f"{row['platform']} post '{label}': {row['data']}")
        return "\n".join(lines) or "No metrics recorded yet."

    # --- the cycle -------------------------------------------------------
    def run_cycle(self) -> CycleReport:
        report = CycleReport()
        self.publish_due(report)
        for name, platform in self.platforms.items():
            try:
                self.process_inbound(name, platform, report)
            except PlatformError as e:
                report.errors.append(f"{name} comments: {e}")
            try:
                self.record_metrics(name, platform)
            except PlatformError as e:
                report.errors.append(f"{name} metrics: {e}")
        log.info("cycle: %s", report.summary())
        return report
