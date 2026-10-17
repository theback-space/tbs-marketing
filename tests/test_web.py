import threading

from fastapi.testclient import TestClient

from tbs_social.web import create_app


def test_dashboard_flow(make_engine):
    engine = make_engine()
    client = TestClient(create_app(engine, threading.Lock()))
    assert client.get("/").status_code == 200
    r = client.post("/cycle")
    assert r.status_code == 200 and "Monitoring cycle done" in r.text
    inbox = client.get("/inbox")
    assert "needs staff" in inbox.text and "health details" in inbox.text

    row = next(r for r in engine.db.list_interactions() if r["author"] == "sam_lifts")
    blocked = client.post(f"/inbox/{row['id']}/reply", data={"text": "Hope your treatment went well"})
    assert "Not sent" in blocked.text
    ok = client.post(f"/inbox/{row['id']}/reply", data={"text": "Thank you!"})
    assert "Reply sent" in ok.text

    client.post("/content/generate", data={"platform": "mock", "count": 2, "topic": ""})
    drafts = engine.db.list_posts("draft")
    assert len(drafts) == 2
    client.post(f"/content/{drafts[0]['id']}/approve", data={"body": drafts[0]["body"]})
    assert engine.db.get_post(drafts[0]["id"])["status"] == "approved"
    assert client.get("/activity").status_code == 200


def test_password_required_when_set(make_engine):
    from dataclasses import replace
    engine = make_engine()
    engine.settings = replace(engine.settings, dashboard_password="s3cret")
    client = TestClient(create_app(engine))
    assert client.get("/").status_code == 401
    assert client.get("/", auth=("admin", "s3cret")).status_code == 200
