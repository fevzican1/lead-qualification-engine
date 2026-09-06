"""
Continuous loop: refill the lead queue, then qualify and submit.

Each cycle:
  1. reload B2B/Oracle knowledge
  2. ingest Common Crawl feed (zero Oracle HTTP)
  3. catalog dump into the queue (zero HTTP) until QUEUE_MAX
  4. HTTP discovery only if fuel is thin AND probe budget remains
  5. pipeline.py --submit (Chromium slice, daily/hourly form caps)
  6. sleep 90s while the hour is open — HTTP empty does not park the loop.
     Sleep until next UTC hour only on hourly form cap; until midnight on daily form cap.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import config
import domain_store
import knowledge
import lead_discovery
import owner_notify

logger = logging.getLogger(__name__)
PYTHON = Path(sys.executable)


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if sys.platform == "win32":
            proc.kill()
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        proc.wait(timeout=15)
    except Exception:
        pass


def _run(script: str, extra: list[str] | None = None, *, timeout: int | None = None) -> int:
    args = [str(PYTHON), str(config.ROOT / script)]
    if extra:
        args.extend(extra)
    logger.info("Running: %s", " ".join(args))
    env = os.environ.copy()
    env.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(config.ROOT / ".playwright"))
    try:
        if timeout:
            proc = subprocess.Popen(
                args,
                cwd=str(config.ROOT),
                env=env,
                start_new_session=True,
            )
            try:
                return int(proc.wait(timeout=timeout))
            except subprocess.TimeoutExpired:
                logger.error("%s exceeded %ss — killed hung Chromium", script, timeout)
                _kill_tree(proc)
                return 124
        completed = subprocess.run(
            args,
            cwd=str(config.ROOT),
            env=env,
            check=False,
        )
        return int(completed.returncode)
    except Exception:
        logger.exception("Failed to launch %s", script)
        return 1


_starve_pinged_hour: list[str] = []


def _hourly_floor() -> int:
    floor = int(getattr(config, "HOURLY_SUBMIT_FLOOR", 30) or 30)
    return min(floor, int(knowledge.hourly_cap()))


def _fuel_target() -> int:
    return domain_store.chromium_fuel_target()


def _warn_if_starving(*, feed_updated_at: str = "") -> None:
    """Tell the owner when fuel is too thin for the hourly floor — after feed sync."""
    _today_n, hour_n = knowledge.submit_counts()
    need = max(0, _hourly_floor() - hour_n)
    if need <= 0:
        return
    fuel = domain_store.chromium_fuel_count()
    # Roughly a third of visits are CAPTCHA / no-form, so budget 3 hosts per post.
    if fuel >= need * 3:
        return
    stamp = time.strftime("%Y-%m-%dT%H", time.gmtime())
    if stamp in _starve_pinged_hour:
        return
    _starve_pinged_hour.append(stamp)
    del _starve_pinged_hour[:-6]
    feed_line = f"GitHub feed: {feed_updated_at or 'bilinmiyor'}\n" if feed_updated_at else ""
    msg = (
        "Yakıt düşük — saatlik taban riski.\n"
        f"Bu saat form: {hour_n}/{knowledge.hourly_cap()} (taban {_hourly_floor()})\n"
        f"Chromium yakıtı: {fuel} host (gereken ~{need * 3})\n"
        f"Kuyruk: {domain_store.queue_depth()}\n"
        f"{feed_line}"
        "Oracle her tur GitHub feed çeker; yeni host yoksa GitHub harvest beklenir.\n"
        "(Harvest Oracle'da değil — GitHub Actions'ta çalışır.)"
    )
    logger.warning("Queue starving: fuel=%s need=%s", fuel, need * 3)
    owner_notify.send(msg)


def _sleep_after_cycle() -> int:
    today_n, hour_n = knowledge.submit_counts()
    daily = knowledge.daily_cap()
    hourly = knowledge.hourly_cap()
    fuel = domain_store.chromium_fuel_count()
    if today_n >= daily:
        wait = min(knowledge.seconds_until_utc_midnight(), max(config.AUTO_RUNNER_SLEEP_SECONDS, 3600))
        logger.info("Daily cap %s/%s — sleeping %ss until a new UTC day", today_n, daily, wait)
        return wait
    if hour_n >= hourly:
        # Rolling window: wait only until the oldest submit ages out, then resume.
        wait = min(300, knowledge.seconds_until_hour_slot())
        logger.info("Hourly cap %s/%s — slot opens in ~%ss", hour_n, hourly, wait)
        return wait
    target = _fuel_target()
    # HTTP empty does not park the machine. Fill the hour toward the floor first.
    if fuel < target and hour_n < _hourly_floor():
        wait = 20
    elif hour_n < _hourly_floor() and fuel > 0:
        wait = 20
    elif hour_n < hourly:
        wait = 25
    else:
        wait = 60
    logger.info(
        "Hour open %s/%s fuel=%s queue=%s http=%s — next cycle in %ss",
        hour_n,
        hourly,
        fuel,
        domain_store.queue_depth(),
        domain_store.http_budget_label(),
        wait,
    )
    return wait


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    os.chdir(config.ROOT)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    print("--- TAM OTONOM SATIS MOTORU BASLATILDI ---")
    cycle = 0
    while True:
        cycle += 1
        print(f"\n=== Tur {cycle} ===")
        knowledge.reload_overlays()
        knowledge.refresh()
        domain_store.hydrate_from_leads()
        pruned = domain_store.prune_dead_queue()
        if pruned:
            print(f"Ölü kuyruk budandı: {pruned} CAPTCHA/formsuz host düştü")
        giants = domain_store.prune_enterprise_queue()
        if giants:
            print(f"Dev perakende kuyruktan çıktı: {giants}")
        junk = domain_store.prune_noise_queue()
        if junk:
            print(f"Demo/hash Shopify kuyruktan çıktı: {junk}")
        unverified = domain_store.purge_unverified_queue()
        if unverified:
            print(f"Form doğrulanmamış {unverified} feed satırı kuyruktan düştü")
        reclaimed = domain_store.reclaim_false_kills()
        if reclaimed:
            print(f"Yanlış ölü işaretlenen {reclaimed} domain geri alındı")
        clamped = domain_store.clamp_long_defers()
        if clamped:
            print(f"{clamped} uzun erteleme kısaltıldı (max {int(getattr(config, 'DEFER_MINUTES', 20) or 20)} dk)")

        target = int(getattr(config, "QUEUE_TARGET", 150) or 150)
        cap = int(getattr(config, "QUEUE_MAX", 250) or 250)
        min_easy = int(getattr(config, "EASY_SCORE_MIN", 55) or 55)
        dumped = domain_store.evict_below(min_easy)
        if dumped:
            print(f"Düşük skorlu kuyruk atıldı: {dumped} (HTTP yok, yer açıldı)")

        smb_early = bool(getattr(config, "SMB_LANE_ENABLED", False))
        print("\n[0/3] Dış feed (Common Crawl / GitHub, Oracle HTTP yok)...")
        feed_stamp = ""
        if smb_early:
            try:
                import feed_ingest
                import target_pool

                pool_stats = target_pool.sync()
                if pool_stats["approved"] or pool_stats["promoted"]:
                    print(
                        f"Target pool auto-approve approved={pool_stats['approved']} "
                        f"promoted={pool_stats['promoted']}"
                    )
                fuel_now = domain_store.chromium_fuel_count()
                fuel_target = domain_store.chromium_fuel_target()
                force_low = (
                    domain_store.queue_depth()
                    < int(getattr(config, "QUEUE_REFILL_BELOW", 80) or 80)
                    or fuel_now < fuel_target
                )
                # The 5-minute devsolve-feed-sync timer already refreshes the feed;
                # the runner re-syncs inline only when the tank is thin or every
                # 3rd cycle so Chromium gets the lion's share of each cycle.
                synced = None
                if force_low or cycle % 3 == 1:
                    synced = feed_ingest.sync_github_feed()
                feed_stamp = str((synced or {}).get("updated_at") or "")
                if synced:
                    print(f"GitHub feed senkron: {synced.get('count', 0)} host, updated={feed_stamp or '?'}")
                fed = feed_ingest.ingest(force_low=force_low)
                if domain_store.chromium_fuel_count() < fuel_target:
                    # Tank still thin after one pass — pull the freshest feed and burst again.
                    synced2 = feed_ingest.sync_github_feed()
                    if synced2:
                        feed_stamp = str(synced2.get("updated_at") or feed_stamp)
                        fed += feed_ingest.ingest(force_low=True)
                print(f"Feed +{fed} | kuyruk={domain_store.queue_depth()}/{cap} | fuel={domain_store.chromium_fuel_count()}")
            except Exception:
                logger.exception("Feed ingest failed — catalog/heal still run")
        else:
            print("SMB feed atlandı (şerit kapalı) — kurumsal feed [FAZ-C] bloğunda çekilir.")

        _warn_if_starving(feed_updated_at=feed_stamp)

        smb = bool(getattr(config, "SMB_LANE_ENABLED", False))
        pipeline_code = 0
        if smb:
            print("\n[1/3] Katalog kuyruğa basılıyor (HTTP yok, kota yanmaz)...")
            finder_code = _run("lead_finder.py")
            if finder_code != 0:
                logger.warning("lead_finder exited %s", finder_code)
            print(f"Kuyruk={domain_store.queue_depth()}/{cap} (hedef {target})")

            print("\n[2/3] Keşif (hazır nitelikli kuyruk inceyse, 1x HEAD)...")
            http_left = domain_store.http_budget_remaining(role="discovery")
            ready = domain_store.ready_pool_size()
            fuel80 = domain_store.chromium_fuel_count(min_easy=int(getattr(config, "FEED_MIN_SCORE", 80) or 80))
            fuel = domain_store.chromium_fuel_count()
            print(
                f"Hazır nitelikli={ready} | fuel80={fuel80} | chromium_fuel={fuel} | "
                f"kuyruk={domain_store.queue_depth()}/{cap}"
            )
            fuel_target = _fuel_target()
            needs_fuel = fuel < fuel_target or fuel80 < fuel_target
            topup = 0
            if not needs_fuel and http_left >= 4:
                _t, hour_now = knowledge.submit_counts()
                if hour_now < _hourly_floor():
                    topup = 2
            if http_left >= 2 and (needs_fuel or topup):
                try:
                    added = lead_discovery.heal_queue(topup=topup)
                    print(
                        f"Self-heal +{added} | hazır={domain_store.ready_pool_size()} "
                        f"| fuel={domain_store.chromium_fuel_count()}/{fuel_target} "
                        f"| http={domain_store.http_budget_label()}"
                    )
                except Exception:
                    logger.exception("Heal failed — pipeline still runs")
            else:
                why = (
                    "http saatlik/günlük tavan"
                    if http_left < 2
                    else f"fuel yeterli (>={fuel_target}), saat tabanda değil — keşif ertelendi"
                )
                print(
                    f"Discovery atlandı ({why}; hazır={ready}, fuel={fuel}/{fuel_target}, "
                    f"kuyruk={domain_store.queue_depth()}/{cap}, "
                    f"http={domain_store.http_budget_label()})"
                )
        else:
            # Faz C: SMB müşteri-bulma şeridi kapalı. Keşif GitHub'da (Actions
            # enterprise harvest) yapılır; Oracle yalnızca feed dosyasını çeker.
            print(
                "\n[FAZ-C] SMB şeridi kapalı — kurumsal contractor kanalı birincil "
                f"(kuyruk={domain_store.queue_depth()}/{cap}, http={domain_store.http_budget_label()})"
            )
            try:
                ent_sync = feed_ingest.sync_enterprise_feed()
                if ent_sync:
                    print(
                        f"Kurumsal feed: {ent_sync['count']} hedef, "
                        f"updated={ent_sync['updated_at'] or '?'}"
                    )
                else:
                    print("Kurumsal feed: değişiklik yok / yapılandırılmadı")
            except Exception:
                logger.exception("Enterprise feed sync failed — only a still-fresh verified feed may be used")

        if not knowledge.oracle_safe():
            logger.warning("Oracle RAM tight — skip Chromium this hour")
            owner_notify.send("RAM sıkışık — bu tur Chromium atlandı, 1 saat sonra tekrar.")
            time.sleep(3600)
            continue

        if smb:
            print("\n[3/3] Formlar dolduruluyor...")
            # Each page operation has its own bounded Playwright timeout. Do not
            # kill the whole visit batch using a fixed wall-clock limit: the
            # hourly-floor visit budget can legitimately be 72–96 hosts.
            pipeline_code = _run(
                "pipeline.py",
                ["--targets", str(config.TARGETS_PATH), "--submit"],
                timeout=None,
            )
            if pipeline_code != 0:
                logger.warning("pipeline exited %s — will retry next cycle", pipeline_code)
                owner_notify.send(f"Pipeline turu hata ile bitti (kod {pipeline_code}). Sonraki tur denenecek.")

        # Faz A — kurumsal contractor başvuru kanalı. Aynı Oracle kotasını
        # paylaşır: sub-cap'li (gün 4 / saat 2), kota daralırsa pipeline önce.
        try:
            import enterprise_apply

            ent = enterprise_apply.run_batch()
            if ent.get("ran"):
                print(
                    f"[FAZ-A] Kurumsal başvuru: {ent.get('applied', 0)} onaylı, "
                    f"{ent.get('skipped', 0)} atlandı"
                )
            else:
                print(f"[FAZ-A] Atlandı: {ent.get('why', '?')}")
        except Exception:
            logger.exception("Enterprise apply failed — pipeline unaffected")

        wait = _sleep_after_cycle()
        print(f"\n[BILGI] Tur {cycle} tamamlandı. kuyruk={domain_store.queue_depth()}/{cap}. {wait}s sonra yeni tur...")
        today_n, hour_n = knowledge.submit_counts()
        if wait >= 300 or pipeline_code != 0 or today_n >= knowledge.daily_cap() or hour_n >= knowledge.hourly_cap():
            owner_notify.send(
                f"Tur {cycle} kapandı. Kuyruk={domain_store.queue_depth()}/{cap}. "
                f"Form saat {hour_n}/{knowledge.hourly_cap()} gün {today_n}/{knowledge.daily_cap()}. "
                f"{max(1, wait // 60)} dk sonra yeni tur."
            )
        time.sleep(wait)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAuto runner stopped.")
        sys.exit(130)
