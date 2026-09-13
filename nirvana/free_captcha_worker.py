"""Lane V-W — free_captcha_worker [Oracle VM, light timer].

Dedicated CAPTCHA Worker (ikincil kuyruk motoru): SADECE captcha_queue'yu
dinler, ana form serisiyle karismaz.

- stealth_former CAPTCHA gorunce sayfayi kapatip ATLAMAZ; hedefi
  captcha_queue'ya yazar ('queued_captcha').
- Bu modul tur basina kucuk dilimi max 2 eszamanli worker ile tuketir
  (CAPTCHA_MAX_WORKERS = 2, asla yukseltilemez).
- Arka planda OpenCV + Tesseract OCR pipeline calisir; gorsel/slider
  dogrulama tamamlaninca form derhal teslim edilir.
- Parali API cagrisi YOK ($0). Watchdog sogutmadaysa veya gunluk 400
  form kotasi dolduysa calismaz (kota/limit korumasi).
"""
from __future__ import annotations

from typing import Any

from nirvana.free_captcha_solver import (
    CAPTCHA_MAX_WORKERS,
    CAPTCHA_QUEUE_NAME,
    CAPTCHA_RUN_LIMIT,
    queue_stats,
    run_captcha_worker,
)


def run_batch(*, limit: int = CAPTCHA_RUN_LIMIT, **kwargs: Any) -> dict[str, Any]:
    """Captcha kuyrugu turu: max 2 eszamanli, kucuk dilim, $0 maliyet."""
    out = run_captcha_worker(limit=limit)
    out["module"] = "free_captcha_worker"
    out["queue_name"] = CAPTCHA_QUEUE_NAME
    out["max_workers"] = min(CAPTCHA_MAX_WORKERS, 2)
    return out
