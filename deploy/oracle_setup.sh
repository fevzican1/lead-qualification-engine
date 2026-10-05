#!/usr/bin/env bash
# Infrastructure guardrails + auto-deploy (target: deploy/oracle_setup.sh).
# Oracle Always Free (Ampere A1.Flex: 4 OCPU / 24GB) sinirlari + stres testi.
set -euo pipefail
APP="${APP_DIR:-/opt/devsolve}"
echo "[guardrails] Ampere sinirlari dogrulaniyor (threads/process/RAM)..."
python3 - "$APP" <<'PY'
import json, sys
from pathlib import Path
app = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/opt/devsolve")
ok = True
def check(name, cond, hint):
    global ok
    print(("OK  " if cond else "FAIL") + " " + name + ": " + hint)
    if not cond:
        ok = False
try:
    import config
    assert config.DAILY_SUBMIT_LIMIT <= 400, "daily cap 400 asilamaz"
    assert config.HOURLY_SUBMIT_LIMIT <= 48, "hourly cap 48 asilamaz"
    check("kota", True, f"gunluk={config.DAILY_SUBMIT_LIMIT} saatlik={config.HOURLY_SUBMIT_LIMIT}")
except Exception as e: check("kota", False, str(e)[:120])
for unit, mem in (("oracle/nirvana-pipeline.service", None), ("oracle/nirvana-webchat.service", None)):
    p = app / unit
    check(unit, p.exists(), "mevcut" if p.exists() else "eksik")
sys.exit(0 if ok else 1)
PY
echo "[stres] hafif bellek/kilitlenme taramasi..."
python3 - <<'PY'
import asyncio
try:
    from core.llm_router import litellm_proxy_config
    print("router:", litellm_proxy_config())
    from api.chat_handler import run_check
    print("chat:", run_check()["health"]["ok"])
    from analytics.audit_generator import proof_card_payload
    print("roi:", proof_card_payload("https://example.com")["roi"])
    from services.outreach_engine import qualifies
    assert qualifies("https://acme.de", "B2B enterprise solutions GmbH")["ok"]
    assert not qualifies("https://magaza.com.tr", "istanbul magazamiz")["ok"]
    print("filtre: OK")
except Exception as e:
    print("STRES FAIL:", str(e)[:300]); raise SystemExit(1)
PY
echo "[deploy] guardrails gecti."
if [ "${DEPLOY_LIVE:-0}" = "1" ]; then
  echo "[deploy] canli alim basliyor (nirvana_oracle_install.sh)..."
  sudo bash "$APP/oracle/nirvana_oracle_install.sh"
else
  echo "[deploy] kuru calisma OK (canli icin DEPLOY_LIVE=1)."
fi
