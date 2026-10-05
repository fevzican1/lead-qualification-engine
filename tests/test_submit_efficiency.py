"""Submit verim kurtarma katmani (2026-10 revizyonu).

Kapsam:
- FormNetWatcher: GET-form/thanks navigasyon dogrulamasi (201 vakalik "click did
  not produce a form POST" sinifinin ana kurtaricisi) + yanlis-pozitif kapisi.
- Zorunlu select doldurma: placeholder (value bos) yerine gercek + notr secenek.
- pipeline transient retry: yalniz sinyalsiz hatalar, lead basina 1, tur tavani.
"""
from __future__ import annotations

import inspect
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any


def _fake_page(url: str = "https://form-host.test/contact") -> SimpleNamespace:
    return SimpleNamespace(
        url=url,
        on=lambda *_a, **_k: None,
        remove_listener=lambda *_a, **_k: None,
        wait_for_timeout=lambda _ms: None,
    )


class _Resp:
    def __init__(self, method: str, url: str, status: int, resource_type: str = "document") -> None:
        self.status = status
        self.request = SimpleNamespace(method=method, url=url, resource_type=resource_type)


def test_watcher_counts_navigation_confirm_after_arm() -> None:
    import form_submitter

    watcher = form_submitter.FormNetWatcher(_fake_page())
    watcher.arm_navigation()
    watcher._on_response(_Resp("GET", "https://form-host.test/tesekkurler", 200))
    assert watcher.hit is True
    assert watcher.via == "nav"
    assert watcher.status == 200


def test_watcher_ignores_nav_before_arm_and_same_url() -> None:
    import form_submitter

    watcher = form_submitter.FormNetWatcher(_fake_page())
    watcher._on_response(_Resp("GET", "https://form-host.test/tesekkurler", 200))
    assert watcher.hit is False
    watcher.arm_navigation()
    watcher._on_response(_Resp("GET", "https://form-host.test/contact", 200))
    assert watcher.hit is False, "ayni URL reload dogrulama sayilmamali"


def test_watcher_disable_navigation_blocks_nav_but_not_post() -> None:
    import form_submitter

    watcher = form_submitter.FormNetWatcher(_fake_page())
    watcher.arm_navigation()
    watcher.disable_navigation()
    watcher._on_response(_Resp("GET", "https://form-host.test/iletisim", 200))
    assert watcher.hit is False
    watcher._on_response(_Resp("POST", "https://form-host.test/api/lead", 200, "xhr"))
    assert watcher.hit is True
    assert watcher.via == "post"


def test_watcher_ignores_analytics_and_non_document_get() -> None:
    import form_submitter

    watcher = form_submitter.FormNetWatcher(_fake_page())
    watcher.arm_navigation()
    watcher._on_response(_Resp("POST", "https://form-host.test/api/collect", 200, "fetch"))
    assert watcher.hit is False
    watcher._on_response(_Resp("GET", "https://form-host.test/banner.png", 200, "image"))
    assert watcher.hit is False


def test_pick_select_option_skips_placeholder() -> None:
    import form_submitter

    opts = [
        {"i": 0, "value": "", "label": "Select a country"},
        {"i": 1, "value": "tr", "label": "Turkey"},
    ]
    assert form_submitter._pick_select_option(opts) == 1


def test_pick_select_option_prefers_neutral_real_value() -> None:
    import form_submitter

    opts = [
        {"i": 0, "value": "", "label": "Lütfen seçiniz"},
        {"i": 2, "value": "other", "label": "Other"},
        {"i": 3, "value": "af", "label": "Afghanistan"},
    ]
    assert form_submitter._pick_select_option(opts) == 2


def test_pick_select_option_all_placeholder_returns_minus_one() -> None:
    import form_submitter

    assert form_submitter._pick_select_option([{"i": 0, "value": "", "label": "Seçiniz"}]) == -1


def test_form_handle_and_is_required_are_fail_open() -> None:
    import form_submitter

    assert form_submitter._form_handle([]) is None

    class _Boom:
        def evaluate(self, *_a: Any, **_k: Any) -> Any:
            raise RuntimeError("boom")

    assert form_submitter._is_required(_Boom()) is False


def _lead(**over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "url": "https://form-host.test/contact",
        "status": "skipped_submit_failed",
        "error": "Could not map any visible form fields",
        "submit_attempts": 1,
        "updated_at": (datetime.now(timezone.utc) - timedelta(hours=20)).isoformat(),
    }
    row.update(over)
    return row


def test_transient_retry_requeues_only_signal_free_failures(monkeypatch) -> None:
    import config
    import domain_store
    import pipeline

    monkeypatch.setattr(config, "SUBMIT_TRANSIENT_RETRY_MAX", 10, raising=False)
    monkeypatch.setattr(config, "SUBMIT_TRANSIENT_RETRY_HOURS", 12.0, raising=False)
    monkeypatch.setattr(domain_store, "unmark", lambda _url: None)
    enqueued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        domain_store,
        "enqueue",
        lambda url, **kw: (enqueued.append((url, str(kw.get("source")))), True)[1],
    )

    target = _lead()
    dom_success = _lead(error="DOM success without network confirmation")
    fresh = _lead(updated_at=datetime.now(timezone.utc).isoformat())
    already = _lead(transient_requeues=1)
    timeout = _lead(error="timeout: nav 30000ms")

    rows = pipeline._retry_transient_submit_fails([target, dom_success, fresh, already, timeout])

    assert rows[0]["status"] == "qualified"
    assert rows[0]["submit_attempts"] == 0
    assert rows[0]["transient_requeues"] == 1
    assert enqueued == [("https://form-host.test/contact", "submit-retry")]
    for row in rows[1:]:
        assert row["status"] == "skipped_submit_failed"
    assert "transient_requeues" not in rows[1]
    assert "transient_requeues" not in rows[2]


def test_transient_retry_respects_run_cap(monkeypatch) -> None:
    import config
    import domain_store
    import pipeline

    monkeypatch.setattr(config, "SUBMIT_TRANSIENT_RETRY_MAX", 1, raising=False)
    monkeypatch.setattr(config, "SUBMIT_TRANSIENT_RETRY_HOURS", 12.0, raising=False)
    monkeypatch.setattr(domain_store, "unmark", lambda _url: None)
    monkeypatch.setattr(domain_store, "enqueue", lambda url, **kw: True)

    first = _lead(url="https://a.test/contact")
    second = _lead(url="https://b.test/contact")
    rows = pipeline._retry_transient_submit_fails([first, second])
    assert rows[0]["status"] == "qualified"
    assert rows[1]["status"] == "skipped_submit_failed"


def test_transient_retry_disabled_when_cap_zero(monkeypatch) -> None:
    import config
    import pipeline

    monkeypatch.setattr(config, "SUBMIT_TRANSIENT_RETRY_MAX", 0, raising=False)
    rows = pipeline._retry_transient_submit_fails([_lead()])
    assert rows[0]["status"] == "skipped_submit_failed"


def test_retry_eligibility_rejects_two_attempts_and_timeout() -> None:
    import pipeline

    now = time.time()
    assert pipeline._transient_retry_eligible(_lead(submit_attempts=2), now_ts=now, hours=12) is False
    assert pipeline._transient_retry_eligible(_lead(error="timeout: net"), now_ts=now, hours=12) is False


def test_submit_pipeline_wiring_locked_in_source() -> None:
    """Kurtarma adimlari silinirse test kirmizi olur (regresyon kapisi)."""
    import form_submitter

    src = inspect.getsource(form_submitter)
    assert "Submit cascade A0 scoped" in src
    assert "watcher.arm_navigation()" in src and "watcher.disable_navigation()" in src
    assert "Fill rescue reload" in src
    assert "FORM_INVALID_RECOVERY" in src
    assert "REQUEST_SUBMIT_FORM_JS" in src and "SUBMIT_BUTTON_ON_FORM_JS" in src
