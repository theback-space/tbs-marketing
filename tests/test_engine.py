import pytest

from tbs_social.ai import Triage
from tbs_social.engine import ComplianceBlocked


def interactions(engine):
    return {r["author"]: r for r in engine.db.list_interactions()}


def test_approve_mode_never_acts_on_its_own(make_engine):
    engine = make_engine("approve")
    platform = engine.platforms["mock"]
    for _ in range(3):
        engine.run_cycle()
    assert platform.likes == [] and platform.replies == []
    assert engine.db.list_actions() == []
    rows = interactions(engine)
    assert rows["sam_lifts"]["draft_reply"]
    assert rows["deals4u"]["status"] == "dismissed"


def test_assist_mode_only_engages_low_risk_praise(make_engine):
    engine = make_engine("assist")
    platform = engine.platforms["mock"]
    for _ in range(3):
        engine.run_cycle()
    rows = interactions(engine)
    liked = {rows[a]["external_id"] for a in ("sam_lifts", "posture_nerd")}
    assert set(platform.likes) == liked
    assert {r[0] for r in platform.replies} == liked
    # health disclosure and complaint wait for staff, untouched
    for author in ("jen.k", "mike_r"):
        assert rows[author]["needs_human"] == 1
        assert rows[author]["liked"] == 0
        assert rows[author]["status"] == "drafted"
    assert rows["jen.k"]["phi_risk"] == 1


def test_phi_overrides_an_ai_that_says_it_is_fine(make_engine):
    class CarelessAI:
        def triage(self, *a, **k):
            return Triage(sentiment="positive", intent="praise", needs_human=False, reason="ok",
                          like=True, reply="Thanks!")

    engine = make_engine("assist", ai=CarelessAI())
    engine.run_cycle()
    row = interactions(engine)["jen.k"]
    assert row["needs_human"] == 1 and row["liked"] == 0
    assert engine.platforms["mock"].likes.count(row["external_id"]) == 0


def test_unsafe_draft_reply_is_held_for_a_human(make_engine):
    class LeakyAI:
        def triage(self, *a, **k):
            return Triage(sentiment="positive", intent="praise", needs_human=False, reason="ok",
                          like=False, reply="Glad your treatment helped!")

    engine = make_engine("assist", ai=LeakyAI())
    engine.run_cycle()
    assert engine.platforms["mock"].replies == []
    assert interactions(engine)["sam_lifts"]["needs_human"] == 1


def test_triage_failure_flags_instead_of_crashing(make_engine):
    class BrokenAI:
        def triage(self, *a, **k):
            raise RuntimeError("API down")

    engine = make_engine("assist", ai=BrokenAI())
    report = engine.run_cycle()
    assert report.flagged == report.new_interactions == 3


def test_manual_reply_is_rechecked(make_engine):
    engine = make_engine()
    engine.run_cycle()
    iid = interactions(engine)["sam_lifts"]["id"]
    with pytest.raises(ComplianceBlocked):
        engine.send_reply(iid, "See you at your next appointment!", automated=False)
    engine.send_reply(iid, "Thank you!", automated=False)
    assert engine.db.get_interaction(iid)["status"] == "replied"


def test_cannot_like_comment_with_health_details(make_engine):
    engine = make_engine()
    engine.run_cycle()
    with pytest.raises(ComplianceBlocked):
        engine.like(interactions(engine)["jen.k"]["id"], automated=False)


def test_draft_approve_publish_and_metrics(make_engine):
    engine = make_engine()
    [pid] = engine.generate_drafts("mock", 1, "desk setup")
    assert engine.db.get_post(pid)["status"] == "draft"
    engine.run_cycle()
    assert engine.db.get_post(pid)["status"] == "draft"  # never publishes unapproved
    engine.approve_post(pid)
    engine.run_cycle()
    post = engine.db.get_post(pid)
    assert post["status"] == "published" and post["external_id"] in engine.platforms["mock"].posts
    assert any(m["external_id"] == post["external_id"] for m in engine.db.latest_metrics("post"))


def test_future_scheduled_post_waits(make_engine):
    engine = make_engine()
    [pid] = engine.generate_drafts("mock", 1, None)
    engine.approve_post(pid, scheduled_for="2999-01-01T09:00:00+00:00")
    engine.run_cycle()
    assert engine.db.get_post(pid)["status"] == "approved"


def test_banned_phrase_blocks_approval(make_engine):
    engine = make_engine()
    [pid] = engine.generate_drafts("mock", 1, None)
    with pytest.raises(ComplianceBlocked):
        engine.approve_post(pid, body="A guaranteed fix for back pain")
