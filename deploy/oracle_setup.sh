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
# --- Ampere A1.Flex (4 OCPU / 24GB) izolator sinirlari ---------------------
try:
    ram = int(getattr(config, "ORACLE_RAM_LIMIT_MB", 0))
    thr = int(getattr(config, "ORACLE_MAX_THREADS", 0))
    check("ram-isolator", 0 < ram <= 24576, f"RAM limit {ram}MB (<=24576)")
    check("thread-isolator", 0 < thr <= 32, f"thread limit {thr} (<=32)")
    # Ollama ile paylasilan bellek: model + webchat + pipeline 24GB icinde
    check("toplam-butce", ram <= 24576 and thr <= 32, "Ampere butcesi icinde")
except Exception as e:
    check("isolator", False, str(e)[:120])
sys.exit(0 if ok else 1)
PY
echo "[ollama] yuksek trafik systemd yapilandirmasi (keep_alive/paralel)..."
OLLAMA_DROPIN_DIR="/etc/systemd/system/ollama.service.d"
OLLAMA_DROPIN="$OLLAMA_DROPIN_DIR/nirvana-perf.conf"
OLLAMA_UNIT_OK=0
if command -v systemctl >/dev/null 2>&1 && [ "$(id -u)" -eq 0 -o -n "$(command -v sudo)" ]; then
  # keep_alive=-1  -> model RAM'de KILITLI, cold-start 0ms
  # num_parallel=4 -> 4 musteri sohbeti AYNI anda siraya girmeden islenir
  install -d "$OLLAMA_DROPIN_DIR"
  cat > "$OLLAMA_DROPIN" <<'EOF'
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11434"
Environment="OLLAMA_KEEP_ALIVE=-1"
Environment="OLLAMA_NUM_PARALLEL=4"
Environment="OLLAMA_MAX_LOADED_MODELS=2"
Environment="OLLAMA_NUM_THREAD=4"
EOF
  systemctl daemon-reload || true
  systemctl restart ollama || true
  OLLAMA_UNIT_OK=1
  echo "ollama drop-in yazildi: $OLLAMA_DROPIN"
  grep -E 'OLLAMA_(KEEP_ALIVE|NUM_PARALLEL)' "$OLLAMA_DROPIN" || true
else
  echo "::uyari::systemd yok ya da yetki yok; ollama drop-in ATLANDI (elle uygulanmali)"
fi
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
