"""Outbound B2B Lead Intake & Queue Engine (target: services/outreach_engine.py).

Thin adapter over existing lanes: task_queue (WAL zero-drop) + domain_store +
lead_discovery/feed_ingest/local_fuel feeder chain + knowledge caps (400/day).
Only Tier-1 B2B (US, EU, DACH, Nordic, 5kEUR+ capacity). TR + B2C excluded.
"""
from __future__ import annotations
import argparse, asyncio, logging, re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse
logger = logging.getLogger(__name__)
TIER1 = frozenset({"de","at","ch","se","no","dk","fi","is","fr","nl","be","lu",
"ie","es","it","pt","pl","cz","sk","hu","ro","bg","hr","si","ee","lv","lt",
"gr","cy","mt","uk","co.uk","us"})
GENERIC = frozenset({"com","net","io","co","app","eu","org","biz"})
MIN_BUDGET_EUR = 5000
_TR_HOST = re.compile(r"\.tr\.?$|\.com\.tr\.?$", re.I)
_TR_TXT = re.compile("çğıöşüÇĞİÖŞÜ|iletisim|iletişim|hizmetler|firmas|sipariş|odeme|ödeme|turkiye|türkiye|magaza|mağaza", re.I)
_B2C = re.compile(r"blogspot|wixsite|linktr|instagram|facebook|tiktok|personal blog", re.I)
def _host(u: str) -> str:
    try: return (urlparse(u).hostname or "").lower()
    except ValueError: return ""
def is_excluded(url: str, text: str = "", excl=("TR",)) -> bool:
    if "TR" not in {s.upper() for s in excl}: return False
    try:
        from core import premium_markets as _pm  # type: ignore
        if _pm.is_turkey(url, text):
            return True
    except Exception:
        pass
    h = _host(url)
    if _TR_HOST.search(h): return True
    blob = f"{h} {text}"
    return bool(_TR_TXT.search(blob) and h.split(".")[-1] not in TIER1)
def is_tier1(url: str, text: str = "") -> bool:
    h = _host(url)
    if not h: return False
    p = h.split("."); s2 = ".".join(p[-2:]) if len(p) > 1 else ""
    if s2 in TIER1 or p[-1] in TIER1: return True
    if p[-1] in GENERIC and not _TR_TXT.search(f"{h} {text}"): return True
    return False
def is_b2c(url: str, text: str = "") -> bool:
    return bool(_B2C.search(f"{url} {text}"))
def qualifies(url: str, text: str = "", budget: int = 5000, excl=("TR",)) -> dict:
    if is_excluded(url, text, excl): return {"ok": False, "reason": "excluded_region"}
    if is_b2c(url, text): return {"ok": False, "reason": "b2c_excluded"}
    if not is_tier1(url, text): return {"ok": False, "reason": "non_tier1"}
    if int(budget or 0) < MIN_BUDGET_EUR: return {"ok": False, "reason": "budget"}
    return {"ok": True, "reason": "tier1_b2b"}
@dataclass
class IntakeStats:
    enqueued: int = 0; dropped: int = 0; fed: int = 0
class OutreachIntake:
    KIND = "b2b_outreach"
    def __init__(self, excl=("TR",), buf: int = 500):
        self.excl = tuple(excl); self.q: asyncio.Queue = asyncio.Queue(maxsize=buf)
        self.stats = IntakeStats()
    async def submit(self, lead: dict) -> bool:
        url = str(lead.get("url") or "")
        txt = f"{lead.get('company_name') or ''} {lead.get('description') or ''}"
        v = qualifies(url, txt, int(lead.get("budget_eur") or 5000), self.excl)
        if not v["ok"]:
            self.stats.dropped += 1; logger.info("drop %s %s", url, v["reason"]); return False
        try:
            import task_queue
            task_queue.enqueue(self.KIND, {"url": url, "easy_score": int(lead.get("easy_score") or 0)})
        except Exception: logger.warning("task_queue write failed", exc_info=True)
        try:
            import domain_store
            domain_store.enqueue(url, source="outreach-intake", easy_score=int(lead.get("easy_score") or 0))
        except Exception: pass
        try: self.q.put_nowait({"url": url, **lead})
        except asyncio.QueueFull: logger.info("buffer full, durable kept %s", url)
        self.stats.enqueued += 1; return True
    async def feed_cycle(self, batch: int = 40) -> int:
        added = 0
        try:
            import lead_discovery as ld
            if not hasattr(ld, "should_run") or ld.should_run():
                added += int(ld.run_discovery(max_new=batch) or 0)
        except Exception: logger.debug("ld skip", exc_info=True)
        try:
            import feed_ingest as fi; added += int(fi.ingest() or 0)
        except Exception: logger.debug("fi skip", exc_info=True)
        try:
            from nirvana import local_fuel as lf
            r = lf.run_batch(dry_run=False) if hasattr(lf, "run_batch") else {}
            added += int((r or {}).get("added", 0) or 0)
        except Exception: logger.debug("lf skip", exc_info=True)
        self.stats.fed += added; return added
    def quota(self) -> dict:
        try:
            import knowledge
            t, h = knowledge.submit_counts()
            return {"daily_cap": knowledge.daily_cap(), "hourly_cap": knowledge.hourly_cap(), "today": t, "hour": h}
        except Exception: return {"daily_cap": 400, "hourly_cap": 48, "today": 0, "hour": 0}
async def run_once(batch: int = 40, excl=("TR",)) -> dict:
    it = OutreachIntake(excl=excl); fed = await it.feed_cycle(batch=batch); q = it.quota()
    return {"fed": fed, "quota": q, "room_day": max(0, q["daily_cap"]-q["today"]), "stats": it.stats.__dict__}
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="services.outreach_engine")
    ap.add_argument("--once", action="store_true"); ap.add_argument("--daemon", action="store_true")
    ap.add_argument("--batch", type=int, default=40); ap.add_argument("--exclude-region", default="TR")
    a = ap.parse_args(argv); regs = tuple(r.strip().upper() for r in a.exclude_region.split(",") if r.strip())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    async def _run():
        if a.daemon:
            tot = 0
            while True:
                r = await run_once(batch=a.batch, excl=regs); tot += r["fed"]
                if r["room_day"] <= 0: return {"fed_total": tot, "quota_full": True}
                await asyncio.sleep(300)
        return await run_once(batch=a.batch, excl=regs)
    print(asyncio.run(_run())); return 0
if __name__ == "__main__": raise SystemExit(main())
