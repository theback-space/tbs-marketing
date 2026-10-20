"""Dashboard: metrics overview, comment inbox with approve/edit/send, content calendar, audit log."""

from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from tbs_social.engine import ComplianceBlocked, Engine
from tbs_social.platforms.base import PlatformError

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["fromjson"] = json.loads


def create_app(engine: Engine, lock: threading.Lock | None = None) -> FastAPI:
    app = FastAPI(title="TBS Social", docs_url=None, redoc_url=None)
    lock = lock or threading.Lock()
    settings = engine.settings
    security = HTTPBasic(auto_error=False)
    state: dict[str, str] = {"summary": ""}
    manual = [p for p in settings.manual_platforms if p not in engine.platforms]

    def auth(creds: HTTPBasicCredentials | None = Depends(security)) -> None:
        if not settings.dashboard_password:
            return  # localhost-only mode; the CLI refuses to bind publicly without a password
        ok = creds is not None and secrets.compare_digest(creds.username, settings.dashboard_user) \
            and secrets.compare_digest(creds.password, settings.dashboard_password)
        if not ok:
            raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})

    def back(path: str, msg: str = "", error: bool = False) -> RedirectResponse:
        if msg:
            sep = "&" if "?" in path else "?"
            path = f"{path}{sep}{'err' if error else 'msg'}={msg}"
        return RedirectResponse(path, status_code=303)

    def render(request: Request, name: str, **ctx) -> HTMLResponse:
        db = engine.db
        attention = sum(1 for r in db.list_interactions(("drafted",)) if r["needs_human"])
        return templates.TemplateResponse(request, name, {
            "brand": engine.brand, "settings": settings, "platforms": [*engine.platforms, *manual],
            "manual": manual,
            "attention": attention, "drafts": len(db.list_posts("draft")),
            "msg": request.query_params.get("msg"), "err": request.query_params.get("err"), **ctx})

    @app.get("/", response_class=HTMLResponse, dependencies=[Depends(auth)])
    def overview(request: Request):
        db = engine.db
        accounts = db.latest_metrics("account")
        history = {a["platform"]: db.metric_history(a["platform"], "account", "account") for a in accounts}
        posts = {p["external_id"]: p for p in db.list_posts("published") if p["external_id"]}
        top = sorted(db.latest_metrics("post"),
                     key=lambda r: sum(v for v in r["data"].values() if isinstance(v, (int, float))),
                     reverse=True)[:8]
        return render(request, "overview.html", accounts=accounts, history=history, top=top,
                      posts=posts, summary=state["summary"])

    @app.get("/inbox", response_class=HTMLResponse, dependencies=[Depends(auth)])
    def inbox(request: Request, show: str = "open"):
        statuses = ("drafted", "new") if show == "open" else None
        return render(request, "inbox.html", rows=engine.db.list_interactions(statuses), show=show,
                      can_like={n: p.can_like_comments for n, p in engine.platforms.items()})

    @app.post("/inbox/{iid}/reply", dependencies=[Depends(auth)])
    def reply(iid: int, text: str = Form(...)):
        with lock:
            try:
                engine.send_reply(iid, text.strip(), automated=False)
            except ComplianceBlocked as e:
                engine.db.update_interaction(iid, draft_reply=text)
                return back("/inbox", f"Not sent — {e}. Edit the reply and try again.", error=True)
            except PlatformError as e:
                return back("/inbox", f"Platform error: {e}", error=True)
        return back("/inbox", "Reply sent")

    @app.post("/inbox/{iid}/like", dependencies=[Depends(auth)])
    def like(iid: int):
        with lock:
            try:
                engine.like(iid, automated=False)
            except (ComplianceBlocked, PlatformError) as e:
                return back("/inbox", str(e), error=True)
        return back("/inbox", "Liked")

    @app.post("/inbox/{iid}/dismiss", dependencies=[Depends(auth)])
    def dismiss(iid: int):
        with lock:
            engine.dismiss(iid)
        return back("/inbox", "Dismissed")

    @app.get("/content", response_class=HTMLResponse, dependencies=[Depends(auth)])
    def content(request: Request):
        db = engine.db
        return render(request, "content.html", drafts=db.list_posts("draft"),
                      queued=db.list_posts("approved"), published=db.list_posts("published")[:20],
                      failed=db.list_posts("failed"))

    @app.post("/content/generate", dependencies=[Depends(auth)])
    def generate(platform: str = Form(...), count: int = Form(3), topic: str = Form("")):
        if platform not in engine.platforms and platform not in manual:
            return back("/content", f"Unknown platform {platform}", error=True)
        try:
            with lock:
                ids = engine.generate_drafts(platform, max(1, min(count, 10)), topic.strip() or None)
        except Exception as e:  # surface API failures to the user instead of a 500
            return back("/content", f"Generation failed: {e}", error=True)
        return back("/content", f"{len(ids)} drafts created")

    @app.post("/content/{pid}/approve", dependencies=[Depends(auth)])
    def approve(pid: int, body: str = Form(...), image_url: str = Form(""), scheduled_for: str = Form("")):
        when = None
        if scheduled_for:
            # <input type=datetime-local> is local browser time without zone; treat as UTC unless given
            dt = datetime.fromisoformat(scheduled_for)
            when = (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat(timespec="seconds")
        with lock:
            try:
                engine.approve_post(pid, body=body.strip(), image_url=image_url.strip() or None,
                                    scheduled_for=when)
            except ComplianceBlocked as e:
                engine.db.update_post(pid, body=body)
                return back("/content", f"Not approved — {e}", error=True)
        return back("/content", "Approved and queued")

    @app.post("/content/{pid}/posted", dependencies=[Depends(auth)])
    def posted(pid: int, body: str = Form(...)):
        with lock:
            try:
                engine.mark_posted_manually(pid, body.strip())
            except ComplianceBlocked as e:
                engine.db.update_post(pid, body=body)
                return back("/content", f"Not marked — {e}", error=True)
        return back("/content", "Marked as posted")

    @app.post("/content/{pid}/reject", dependencies=[Depends(auth)])
    def reject(pid: int):
        with lock:
            engine.db.update_post(pid, status="rejected")
        return back("/content", "Draft rejected")

    @app.get("/activity", response_class=HTMLResponse, dependencies=[Depends(auth)])
    def activity(request: Request):
        return render(request, "activity.html", actions=engine.db.list_actions(200))

    @app.post("/cycle", dependencies=[Depends(auth)])
    def cycle():
        with lock:
            report = engine.run_cycle()
        return back("/", f"Monitoring cycle done: {report.summary()}", error=bool(report.errors))

    @app.post("/summary", dependencies=[Depends(auth)])
    def summary():
        try:
            state["summary"] = engine.ai.summarize_performance(engine.performance_report())
        except Exception as e:
            return back("/", f"Summary failed: {e}", error=True)
        return back("/")

    return app
