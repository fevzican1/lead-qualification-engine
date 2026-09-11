"""Lane DD — dual_delivery [GitHub Actions + Oracle, light].

Parallel verified-delivery: when form fires successfully, send a DMARC/SPF
verified corporate email as backup channel. Ensures message reaches the
target even if web form silently swallows it.
"""
from __future__ import annotations

import json
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import config
from nirvana.registry import state_path

DUAL_LOG = "dual_delivery_log.json"

SMTP_FALLBACK = {
    "host": "smtp-relay.gmail.com",
    "port": 587,
    "user": config.SENDER_EMAIL,
    "use_tls": True,
}


def _smtp_probe(host: str, port: int, timeout: float = 6.0) -> bool:
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def send_dual_email(
    *,
    to_addr: str,
    subject: str,
    body_text: str,
    body_html: str | None = None,
) -> dict[str, Any]:
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    result = {
        "to": to_addr, "subject": subject, "ts": ts,
        "sent": False, "channel": "smtp", "error": None,
    }
    pwd = getattr(config, "SMTP_PASSWORD", "") or getattr(config, "CORPORATE_SMTP_PASSWORD", "")
    user = SMTP_FALLBACK["user"]
    if not user or not pwd:
        result["error"] = "smtp_credentials_absent"
        _record(result)
        return result
    if not _smtp_probe(SMTP_FALLBACK["host"], SMTP_FALLBACK["port"]):
        result["error"] = "smtp_unreachable"
        _record(result)
        return result

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"DevSolve <{user}>"
    msg["To"] = to_addr
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    if body_html:
        msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_FALLBACK["host"], SMTP_FALLBACK["port"], timeout=10) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(user, pwd)
            s.send_message(msg)
        result["sent"] = True
    except smtplib.SMTPRecipientsRefused:
        result["error"] = "recipient_refused"
    except smtplib.SMTPAuthenticationError:
        result["error"] = "auth_failed"
    except Exception as e:
        result["error"] = str(e)[:120]
    _record(result)
    return result


def _record(result: dict[str, Any]) -> None:
    path = state_path(DUAL_LOG)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = []
    data.append(result)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data[-300:], ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def run_batch(*, to_addr: str = "", subject: str = "", body: str = "",
              **kwargs: Any) -> dict[str, Any]:
    if not to_addr:
        return {"sent": False, "reason": "no_target"}
    return send_dual_email(to_addr=to_addr, subject=subject or "DevSolve — Follow-up",
                           body_text=body or "Form delivery confirmed via web channel.")
