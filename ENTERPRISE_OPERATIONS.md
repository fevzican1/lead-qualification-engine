# Evidence-gated contractor intake

## What this system does (and does not do)

GitHub's existing `enterprise-feed` workflow uses the Remotive public API
(attributed source URL preserved) at 00:33/06:33/12:33/18:33 UTC. GitHub may delay
scheduled jobs. It considers at most 16 recent worldwide contract/freelance
software roles with Python/API/automation/integration signals. Urgency adds
priority, **not an acceptance probability**. Roles are not evidence that the
employer accepts autonomous workers; the application explicitly discloses the
AI-assisted service model and asks whether it is suitable.

One browser, heavy assets blocked, a 10-minute scan budget and 15-minute job
timeout replace the former hourly scan. Oracle only uses a fresh schema-v2 feed
and polls it once per 15-minute runner cycle instead of the legacy 20-second
SMB loop. Sales/support/newsletter forms, closed roles, required CVs,
eligibility questions and required consents are review-only, not auto-filled.
No fallback to unscanned partner URLs. Empty results are valid and stop sending.

Legacy Common Crawl/Tranco harvest, publish, optimizer and watchdog workflows
are retained for rollback but gated by the repository variable
`ENABLE_LEGACY_SMB_WORKFLOWS == 'true'` (disabled by default). Disable these
workflows in GitHub as well and cancel queued/running legacy jobs during the
enterprise cutover; keep `enterprise-feed` enabled. No customer data is deleted.
The old Oracle feed-sync timer exits without network or queue writes when
`SMB_LANE_ENABLED=0`; only the enterprise runner downloads the new feed.
Do not re-enable legacy producers independently of a reviewed lane migration.

Oracle limits are **attempts**: at most 4/day, 2/rolling hour, never increased by
environment overrides. Attempts include failures, are reserved before submission,
and use a cross-process lane lock. Ambiguous writes and previous submissions
are not automatically retried. No-send retries honor `ENTERPRISE_RETRY_SKIP_DAYS=0`
without bypassing caps. One exact form, one native click; no submit cascade.
Submissions share existing global ceilings (400/day, 32/hour) with the legacy
pipeline. Keep `SMB_LANE_ENABLED=0` for this deployment; enterprise writes are
disabled if the legacy lane is enabled, avoiding parallel ledger writers. The existing site-probe
counter (500/day, 22/hour) does not meter feed downloads or all browser traffic.
It is **not an Oracle billing/tenancy-limit audit**. No shape, disk, model or paid
API changes are required by this release.

## Funnel definitions

- `submitted_confirmed`: matching form POST and application thank-you observed.
  Not an interview invitation, acceptance or paid contract.
- `interest_reported`: customer expressed interest, not contract verification.
- `contract_signed`: owner checked signed scope/access references.
- `payment_reported`: customer statement only, never permission to deliver.
- `payment_verified`: owner checked settled provider transaction against the
  chat's recorded amount/currency and unique transaction reference.
- `fulfillment_ready`: verified payment + signed scope + authorized access.
  **There is no autonomous service-delivery worker in this repository.** This
  predicate does not create accounts, grant production access or start work.

The proposal/card is a proposed workflow, not fabricated proof of work. Retainer
proposal remains $2500 USD/month. $5000 requires separately agreed scope and a
matching real Payoneer payment request; changing `.env` cannot change a provider
request. No percentage acceptance or guaranteed income is promised.

## Authorized payment workflow (private owner chat only)

1. Use the Payoneer dashboard to check account eligibility, actual recipient,
   request amount/currency and request expiration. Create/update a request there
   if necessary. Do not append invented amount parameters to the URL.
2. After the customer has started the bot, attest the actual request:
   `/payready CHATID 2500 USD RECIPIENT_LABEL PROVIDER_REQUEST_REFERENCE`
   (single-token labels/references). This is owner attestation, not provider API
   verification. It is bound to one chat and the URL hash, and expires in 30 days.
3. After checking signed scope and access authorization:
   `/approvecontract CHATID SIGNED_CONTRACT_REF SCOPE_REF ACCESS_PERMISSION_REF`
   This binds the contract to the currently configured USD amount.
4. Only a subsequent explicit purchase request can receive the link.
5. Check **settled** payment on the provider dashboard, then:
   `/verifypayment CHATID 2500 USD UNIQUE_PROVIDER_TRANSACTION_REF`
   The reference cannot be used for a second payment. Screenshots/customer text
   alone are not sufficient. Check `fulfillment_ready` before manual kickoff.

No charge, payout or subscription is initiated by these commands. Never store
account passwords, tokens or bank details in public feeds or this document.
`payment_readiness.json` and runtime customer ledgers must remain untracked.

## Validation

Run `python -m pytest -q` from the repository root. Install the existing requirements
and pytest in the development environment first. Browser tests intercept every
request to example domains; no third-party form receives a test submission.

Deployment must use reviewed committed code only, SCP to a staging directory,
compile before restart, and backup the replaced files plus runtime configuration.
Do not upload local runtime JSON or `.env`; never run a blanket setup/clamp script
that could undo current production settings. Rotate any exposed `ADMIN_CODE`
without logging its value; retain the established owner identity.