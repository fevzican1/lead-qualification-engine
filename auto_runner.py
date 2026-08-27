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
        wait = domain_store.seconds_until_next_utc_hour()
        logger.info("Hourly cap %s/%s — sleeping %ss until next UTC hour", hour_n, hourly, wait)
        return wait
    # HTTP empty does not park the machine. Fill the hour toward 20–32 submits.
    if hour_n < 20 and fuel > 0:
        wait = 40
    elif hour_n < hourly:
        wait = 60
    else:
        wait = 90
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

        print("\n[0/3] Dış feed (Common Crawl / GitHub, Oracle HTTP yok)...")
        try:
            import feed_ingest

            fed = feed_ingest.ingest()
            print(f"Feed +{fed} | kuyruk={domain_store.queue_depth()}/{cap}")
        except Exception:
            logger.exception("Feed ingest failed — catalog/heal still run")

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
        if http_left >= 2 and fuel80 < knowledge.hourly_cap():
            try:
                added = lead_discovery.heal_queue()
                print(
                    f"Self-heal +{added} | hazır={domain_store.ready_pool_size()} "
                    f"| fuel={domain_store.chromium_fuel_count()} | http={domain_store.http_budget_label()}"
                )
            except Exception:
                logger.exception("Heal failed — pipeline still runs")
        else:
            why = "http saatlik/günlük tavan" if http_left < 2 else "hazır kuyruk yeterli"
            print(
                f"Discovery atlandı ({why}; hazır={ready}, fuel={fuel}, "
                f"kuyruk={domain_store.queue_depth()}/{cap}, "
                f"http={domain_store.http_budget_label()})"
            )

        if not knowledge.oracle_safe():
            logger.warning("Oracle RAM tight — skip Chromium this hour")
            owner_notify.send("RAM sıkışık — bu tur Chromium atlandı, 1 saat sonra tekrar.")
            time.sleep(3600)
            continue

        print("\n[3/3] Formlar dolduruluyor...")
        pipe_timeout = int(getattr(config, "PIPELINE_TIMEOUT_SECONDS", 1500) or 1500)
        pipeline_code = _run(
            "pipeline.py",
            ["--targets", str(config.TARGETS_PATH), "--submit"],
            timeout=pipe_timeout,
        )
        if pipeline_code == 124:
            logger.warning("pipeline hung — killed after %ss", pipe_timeout)
            owner_notify.send(
                f"Pipeline bir sitede takıldı, {pipe_timeout // 60} dk sonra kestik. "
                "Sonraki tur devam edecek — bildirim kesilmesin diye."
            )
        elif pipeline_code != 0:
            logger.warning("pipeline exited %s — will retry next cycle", pipeline_code)
            owner_notify.send(f"Pipeline turu hata ile bitti (kod {pipeline_code}). Sonraki tur denenecek.")

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
