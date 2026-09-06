# Public repository security

This repo is safe to publish **if** the rules below are followed.

## Never commit

- `.env` (gitignored) — Telegram tokens, Payoneer URL, ingest token, Oracle keys
- `owner.json`, `optouts.json`, `leads.json`, `processed_domains.json`, `unprocessed_leads.json`
- SSH private keys, API keys, passwords

## Stored only in GitHub Secrets (Actions)

- `ORACLE_HOST`, `ORACLE_USER`, `ORACLE_SSH_KEY`
- `PAYONEER_LINK` (canlı 2.500 EUR Payoneer talep linki; `nirvana-oracle-deploy` bunu Oracle `.env`'ine yazar. Repoya commit EDİLMEZ.)
- `INGEST_API_TOKEN`
- Optional: `FEED_GITHUB_TOKEN` (not needed for public raw feed URL)

## Stored only on Oracle VM `/opt/devsolve/.env`

- `TELEGRAM_BOT_TOKEN`, `PAYONEER_PAYMENT_URL`, `INGEST_API_TOKEN`
- `TELEGRAM_OWNER_CHAT_ID`, `TELEGRAM_NOTIFY_*`

## Safe in public code

- `feeds/ready_queue.json` — public contact URLs from Common Crawl (no credentials)
- `.env.example` — placeholders only
- Workflow files reference `${{ secrets.* }}` only

## Public feed pull

Oracle and runners use:

`https://raw.githubusercontent.com/fevzican1/lead-qualification-engine/master/feeds/ready_queue.json`

No GitHub token required for read access on a public repo.
