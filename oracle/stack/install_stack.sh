#!/usr/bin/env bash
# Rapor 2.1/2.2 — $0 musteri-hatti servislerini Oracle VM'de ayaga kaldirir.
# Kullanim (sunucuda):  sudo bash oracle/stack/install_stack.sh
# Idempotent: tekrar calistirmak zarar vermez; .env'e eksik anahtarlari ekler.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/devsolve}"
STACK_DIR="$APP_DIR/oracle/stack"

echo "==> 1/5 Docker kontrolu"
if ! command -v docker >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y ca-certificates curl
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker >/dev/null 2>&1 || true

echo "==> 2/5 SearXNG ayar dosyasi"
mkdir -p "$STACK_DIR/searxng"
if [ ! -f "$STACK_DIR/searxng/settings.yml" ]; then
  cat > "$STACK_DIR/searxng/settings.yml" <<'YML'
use_default_settings: true
general:
  instance_name: "DevSolve Enrichment Search"
server:
  secret_key: "degistir-bunu-uzun-rastgele-bir-anahtar"
  limiter: false
search:
  formats: [html, json]
  safe_search: 0
  default_lang: "en"
ui:
  static_use_hash: true
YML
  echo "    settings.yml olusturuldu (secret_key'i degistirin)"
fi

echo "==> 3/5 Stack baslatiliyor (SearXNG + Crawl4AI + Documenso + Formbricks)"
cd "$STACK_DIR"
docker compose -f docker-compose.stack.yml up -d

echo "==> 4/5 .env anahtarlari (idempotent)"
add_env() {
  local key="$1" value="$2"
  if ! grep -q "^${key}=" "$APP_DIR/.env" 2>/dev/null; then
    printf '%s=%s\n' "$key" "$value" >> "$APP_DIR/.env"
    echo "    eklendi: $key"
  fi
}
add_env SEARXNG_URL "http://127.0.0.1:8080"
add_env CRAWL4AI_URL "http://127.0.0.1:11235"
add_env DOCUMENSO_URL "http://127.0.0.1:3001"
add_env DOCUMENSO_CREATE_PATH "/envelope/create"
add_env DOCUMENSO_DISTRIBUTE_PATH "/envelope/distribute"
add_env FORMBRICKS_URL "http://127.0.0.1:3002"

echo "==> 5/5 Saglik ozeti"
for pair in "nv-searxng 8080" "nv-crawl4ai 11235" "nv-documenso 3001" "nv-formbricks 3002"; do
  set -- $pair
  name="$1"; port="$2"
  code=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$port/" || echo "000")
  echo "    $name : HTTP $code"
done

cat <<'EOF'

Sonraki adimlar (panel islemleri — bir kez):
  1) Documenso (http://127.0.0.1:3001, nginx ile sign.<domain>) hesabi ac ->
     sablon olustur -> Settings > API Keys -> anahtari .env'de DOCUMENSO_API_KEY'e yaz
     (v2 anahtari "api_" ile baslar; Authorization basligina ham yazilir).
  2) Documenso sablon ID'sini .env'de DOCUMENSO_TEMPLATE_ID'ye ekle.
  3) Formbricks (http://127.0.0.1:3002) hesabi ac -> onboarding anketi olustur ->
     linkteki ID'yi .env'de FORMBRICKS_SURVEY_ID'ye ekle.
  4) Dis erisim icin nginx ornegi: oracle/stack/nginx-stack.conf
  5) Motoru yeniden baslat: sudo systemctl restart nirvana-pipeline nirvana-salesbot
EOF
