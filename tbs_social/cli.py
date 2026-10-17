"""Command line: serve the dashboard, run the monitor, generate drafts."""

from __future__ import annotations

import argparse
import logging
import threading
import time

from tbs_social.config import Brand, Settings
from tbs_social.db import DB
from tbs_social.engine import Engine
from tbs_social.platforms import build_platforms

log = logging.getLogger("tbs_social")


def build_engine(demo: bool) -> Engine:
    settings = Settings.from_env()
    brand = Brand.load(settings.brand_file)
    if demo:
        from tbs_social.ai import OfflineAI
        ai = OfflineAI(brand)
    else:
        from tbs_social.ai import ClaudeContentAI
        ai = ClaudeContentAI(brand, hipaa=settings.hipaa)
    return Engine(DB(settings.db_path), build_platforms(settings), ai, brand, settings)


def monitor_loop(engine: Engine, every_minutes: float, lock: threading.Lock) -> None:
    while True:
        try:
            with lock:
                report = engine.run_cycle()
            log.info("monitoring cycle: %s", report.summary())
            for err in report.errors:
                log.warning("  %s", err)
        except Exception:
            log.exception("monitoring cycle crashed; will retry next interval")
        time.sleep(every_minutes * 60)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="tbs_social", description=__doc__)
    parser.add_argument("--demo", action="store_true", help="use offline rule-based AI (no API key needed)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the dashboard (and optionally the monitor)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--monitor-every", type=float, default=0, metavar="MIN",
                   help="also run a monitoring cycle every MIN minutes (0 = off)")

    m = sub.add_parser("monitor", help="run monitoring cycles without the dashboard")
    m.add_argument("--every", type=float, default=15, metavar="MIN")
    m.add_argument("--once", action="store_true", help="run a single cycle and exit (for cron)")

    g = sub.add_parser("generate", help="create post drafts for review in the dashboard")
    g.add_argument("platform")
    g.add_argument("--count", type=int, default=3)
    g.add_argument("--topic")

    sub.add_parser("report", help="print the latest metrics and an AI summary")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    engine = build_engine(args.demo)
    lock = threading.Lock()

    if args.cmd == "serve":
        if args.host not in ("127.0.0.1", "localhost") and not engine.settings.dashboard_password:
            parser.error("set TBS_DASHBOARD_PASSWORD before serving on a non-local address")
        import uvicorn
        from tbs_social.web import create_app
        if args.monitor_every > 0:
            threading.Thread(target=monitor_loop, args=(engine, args.monitor_every, lock), daemon=True).start()
        uvicorn.run(create_app(engine, lock), host=args.host, port=args.port)
    elif args.cmd == "monitor":
        if args.once:
            report = engine.run_cycle()
            print(report.summary())
            for err in report.errors:
                print("  error:", err)
        else:
            monitor_loop(engine, args.every, lock)
    elif args.cmd == "generate":
        ids = engine.generate_drafts(args.platform, args.count, args.topic)
        print(f"Created drafts {ids}. Review them in the dashboard under Content.")
    elif args.cmd == "report":
        text = engine.performance_report()
        print(text)
        print()
        print(engine.ai.summarize_performance(text))
