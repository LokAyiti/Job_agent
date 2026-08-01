# Automated Job Application System

End-to-end multi-agent pipeline that discovers jobs, scores fit, tailors resumes, submits applications, and monitors recruiter replies.

## Tracks

- **User Intake** — interactive CLI wizard that produces a unified `profile.json`.
- **Track C — Job Discovery Agent** — powered by [Scrapling](https://github.com/D4Vinci/Scrapling). Discovers jobs from Greenhouse, Lever, GovernmentJobs, LinkedIn (guest API), Indeed (RSS), company career pages, and any URL via the `universal` spider. LinkedIn/Indeed are disabled by default and must be opted in via `profile.json`.
- **Track D — Fit Scoring Agent** — combines LLM semantic scoring with keyword overlap to filter out poor matches before any resume work is done.
- **Track E — Resume Tailoring Engine** — four sub-agents:
  1. JD Analyzer Agent
  2. Resume Retriever Agent
  3. Rewrite / Fabrication Agent (honors `fabrication_tolerance` and preserves dates)
  4. ATS / Recruiter Scoring Agent (with feedback loop)
- **Track B — Action & Persistence Layer** — submission automation, email monitoring, encrypted credential store, proxy rotation, stealth browsing, circuit breakers, structured logging, and Excel/SQLite state.
- **Orchestrator / CEO Agent** — coordinates the full pipeline and gates real submissions behind trusted-platform approval.

## Project layout

```
job_agent/
  agents/
    base_agent.py         # Shared utilities, humanizer, retries
    submission_agent.py   # Fills/submits applications, solves CAPTCHA, manages login
    email_agent.py        # Gmail/Outlook monitor, drafts, responses
    orchestrator.py       # CEO agent
    scoring_agent.py      # LLM + keyword fit scoring
    tailoring_agent.py    # Subprocess bridge to Track E resume engine
  discovery/
    base.py               # JobDiscoverySource protocol
    registry.py           # Central discovery registry
    greenhouse.py         # Greenhouse API discovery (via Scrapling client)
    lever.py              # Lever API discovery (via Scrapling client)
    governmentjobs.py     # GovernmentJobs.com scraper
    company_pages.py      # Generic career-page crawler (via Scrapling client)
    linkedin.py           # LinkedIn discovery (via Scrapling client, disabled by default)
    indeed.py             # Indeed RSS discovery (via Scrapling client, disabled by default)
    universal.py          # Generic Scrapling spider for any career page
  sites/
    base.py               # SiteAdapter protocol
    registry.py           # Central adapter registry (built-in + approved generated adapters)
    adapter_generator.py  # LLM + heuristic adapter generator from DOM snapshots
    approval_registry.py  # Tracks generated-adapter drafts and approvals
    scrapling_mixin.py    # Stealth page snapshots for Cloudflare-protected submissions
    generated_drafts/     # Approved auto-generated adapters
    greenhouse.py         # Greenhouse adapter
    workday.py            # Workday adapter
    icims.py              # iCIMS adapter
  scrapling_client.py     # HTTP client to the Scrapling service
  persistence/
    credentials.py        # Encrypted SQLite account store
    excel_logger.py       # applications.xlsx log
    sqlite_queue.py       # Crash-recovery queue
    google_sync.py        # Google Drive/Sheets sync
  utils/
    encryption.py         # Fernet credential vault
    proxy_rotator.py     # Legacy compatibility wrapper; proxying is now handled by Scrapling
    humanizer.py          # Human-like pacing and typing helpers
    circuit_breaker.py    # Circuit breaker + retry helpers
    structured_logging.py # loguru configuration
  captcha.py              # 2Captcha integration
  config.py               # pydantic-settings + .env
  models.py               # JobApplication, Resume, Account, statuses
  cli.py                  # Command-line entry

scrapling_service/        # FastAPI service running Scrapling fetchers + spiders
  Dockerfile
  main.py
  spiders.py
  proxy.py
  requirements.txt

chrome_extension/         # Browser extension that captures unknown ATS pages
chrome_extension/
  manifest.json
  popup.html
  popup.js
  content.js

job_application_system/
  agents/
    jd_analyzer.py        # Track E JD Analyzer Agent
    resume_retriever.py   # Track E Resume Retriever Agent
    resume_tailor.py      # Track E Rewrite / Fabrication Agent
    resume_builder.py     # DOCX/PDF resume builder
    ats_recruiter_scorer.py  # Track E ATS / Recruiter Scoring Agent
    cover_letter_builder.py  # Cover-letter generator
    consistency_ledger.py    # Maps every generated resume to source + claims
  tailor_bridge.py        # Subprocess bridge called by Track B

scripts/
  benchmark_discovery.py  # Compare Scrapling discovery success rates and timings
  run_scrapling_service.sh # Local dev helper (Unix)
  run_scrapling_service.ps1 # Local dev helper (Windows)

docker-compose.yml        # Two-container orchestration: job-agent + scrapling-service
Dockerfile                # Main job-agent container
```

## Quick start

1. Activate the virtual environment (already created):

```bash
source .venv/Scripts/activate  # Windows Git Bash
# or .venv\Scripts\activate    # Windows Command Prompt
```

2. Install dependencies:

```bash
python -m pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and fill in your real values:

```bash
cp .env.example .env
```

Required for any run:
- `MY_NAME`, `MY_EMAIL`, `MY_PHONE`, `MY_LINKEDIN`
- `RESUME_DIR` (default `resume/` is fine)
- `LOG_FILE` (default `logs/applications.xlsx` is fine)

Optional:
- `LOGIN_EMAIL` + `LOGIN_PASSWORD` — used to create candidate accounts on platforms that require sign-up (Workday, iCIMS, etc.). Encrypted at rest in the local SQLite credential store.
- `TWOCAPTCHA_API_KEY` — automatic CAPTCHA solving; leave blank to rely on human-in-the-loop.
- `OPENROUTER_API_KEY` / `DATABRICKS_TOKEN` + `DATABRICKS_SONNET_ENDPOINT` — LLM providers for scoring, resume tailoring, and adapter generation. Set these in `job_application_system/.env`. OpenRouter is preferred; Databricks is used as a runtime fallback. `PRIMARY_MODEL` defaults to `openai/gpt-4o`.
- Google service-account JSON path + sheet/drive IDs
- Gmail credentials JSON + sender email
- `SCRAPLING_SERVICE_URL` (default `http://localhost:8723`) and `SCRAPLING_USE_SERVICE` (default `false`) — see Scrapling service section.
- `PROXY_LIST` — passed to the Scrapling service fetchers and spiders.

4. Run the intake wizard to create a unified `profile.json`:

```bash
python -m job_agent.cli intake
```

This writes `profile.json` with your target roles, locations, salary floor, base resume library, and fabrication tolerance (`none | moderate | aggressive`).

5. (Optional) Add base resume templates to `base resume/`.

6. Discover jobs and run the full pipeline in dry-run mode:

```bash
python -m job_agent.cli discover --sources greenhouse,lever
python -m job_agent.cli pipeline --sources greenhouse --dry-run
```

7. Check the Excel log:

```bash
python -m job_agent.cli show-config
```

The log is at `logs/applications.xlsx`.

## How the pipeline works

1. **User Intake** writes a single `profile.json` with your targets and fabrication tolerance.
2. **Track C — Job Discovery** routes all scraping through the Scrapling service. Built-in sources cover Greenhouse, Lever, GovernmentJobs, LinkedIn, Indeed, and company career pages. The `universal` source can crawl any career URL using the generic Scrapling spider with adaptive selectors and pause/resume checkpoints. LinkedIn/Indeed are disabled by default and can be enabled in `profile.json`.
3. **Track D — Fit Scoring** combines an LLM semantic score with keyword overlap and drops jobs below `MIN_FIT_SCORE`.
4. **Track E — Resume Tailoring Engine** runs four sub-agents:
   - JD Analyzer extracts structured requirements.
   - Resume Retriever picks the best base template from `base resume/`.
   - Rewrite / Fabrication Agent tailors content to the JD while preserving original employment dates.
   - ATS / Recruiter Scoring Agent validates the draft and loops back with feedback if needed.
   - A **consistency ledger** at `data/consistency_ledger.json` records every generated resume, its source template, tolerance level, and claims.
5. **Track B — Submission & Persistence** fills/submits forms, monitors email, and logs everything to Excel/SQLite.
6. **Orchestrator / CEO Agent** sequences the agents, applies human-approval gates, and promotes platforms to auto-submit only after enough approved successes.

## Safety switches & anti-detection

- `ENABLE_AUTO_SUBMIT=false` (default) makes the Submission Agent stop at the final submit button and log the job as `queued` instead of actually submitting. **Keep this false until you are ready to apply for real.**
- `HUMAN_IN_THE_LOOP=true` pauses and flags jobs as `needs_human` when CAPTCHA cannot be solved automatically, login/account creation fails, or an unsupported form is detected.
- `MAX_RETRIES`, `RETRY_DELAY_SECONDS`, and `DELAY_BETWEEN_JOBS_SECONDS` control rate limiting and retries.
- All browser-based discovery and stealth submission flows are routed through the **Scrapling service** (see below), which handles TLS/JA3 fingerprinting, Cloudflare challenge solving, rotating user agents, and real-browser rendering so the host code does not need to manage anti-detection scripts itself.

## Platform-specific adapters

The Submission Agent picks an adapter based on the job URL:

- `greenhouse` — `boards.greenhouse.io` public application forms.
- `workday` — `myworkdayjobs.com` portals (account creation/login, multi-step wizard aware).
- `icims` — `icims.com` / `applicantpro.com` portals (account creation/login, iframe aware).

For platforms that require a candidate account, the Orchestrator checks the local credential store first. If no account exists, it creates one with `LOGIN_EMAIL`/`LOGIN_PASSWORD` and stores the credentials for reuse.

## CAPTCHA handling

1. If `TWOCAPTCHA_API_KEY` is set, the Submission Agent detects and solves **reCAPTCHA**, **hCaptcha**, and generic **image/grid** CAPTCHAs.
2. For image CAPTCHAs, the agent screenshots the matching `<img>` element, base64-encodes it, and submits it to the 2Captcha `base64` API. The returned text answer is injected into the nearby input field.
3. If 2Captcha fails repeatedly, the circuit breaker opens to avoid burning API credits, and the agent falls back to human-in-the-loop.
4. If 2Captcha is not configured or an unsupported challenge type appears, the agent pauses for manual entry (`HUMAN_IN_THE_LOOP=true`) or flags the job as `needs_human`.

## Track B — Reliability / anti-detection (Action & Persistence Layer)

### Credential encryption

Passwords and profile JSON stored in the SQLite credential database are encrypted with Fernet. The master key is loaded from:

1. `CREDENTIAL_MASTER_KEY` env var (base64 Fernet key) — recommended for production.
2. `CREDENTIAL_KEY_FILE` path.
3. Auto-generated local key file `.credential_key` if neither is provided.

Generate a production key with:

```python
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
```

Then set it in `.env` and delete the auto-generated `.credential_key` file.

### Proxy rotation

Set a comma-separated proxy list in `.env`:

```env
PROXY_LIST=http://proxy1.example.com:8080,user:pass@proxy2.example.com:8080
```

The Scrapling service passes these proxies to its fetcher and spider sessions. The Submission Agent still assigns proxies **deterministically per domain** (`job_agent.utils.proxy_rotator.get_for_domain`) so the same platform always uses the same proxy across sessions, keeping account login sessions stable on ATS portals like Workday and iCIMS. Leave blank to run without a proxy. For Docker, the `scrapling-service` container receives `PROXY_LIST` via `docker-compose.yml`.

### Stealth browsing and human pacing

- `SCRAPLING_USE_SERVICE=true` (the default inside Docker) routes all browser traffic through the Scrapling service, which solves Cloudflare Turnstile/interstitial challenges, spoofs TLS/JA3 fingerprints, and rotates browser fingerprints automatically.
- `USE_STEALTH=true` keeps the legacy local Playwright path enabled only when `SCRAPLING_USE_SERVICE=false`.
- `BROWSER_HEADLESS=true` runs the browser without a UI; set to `false` for manual intervention.
- `HUMANIZER_MIN_DELAY`, `HUMANIZER_MAX_DELAY`, `TYPING_DELAY_MIN`, `TYPING_DELAY_MAX` tune randomized delays between actions and keystrokes.
- `DELAY_BETWEEN_JOBS_SECONDS` + `JITTER_BETWEEN_JOBS=true` randomize wait time between applications to reduce bot fingerprints.

### Retries and circuit breakers

- `MAX_RETRIES` and `RETRY_DELAY_SECONDS` control per-job retry attempts with exponential backoff + jitter.
- **Per-domain circuit breakers** (`job_agent.utils.circuit_breaker.DomainCircuitBreakerRegistry`) protect individual ATS platforms: after a domain crosses `CIRCUIT_BREAKER_FAILURE_THRESHOLD` failures, the Orchestrator skips jobs on that domain for `CIRCUIT_BREAKER_RECOVERY_TIMEOUT` seconds.
- Service-level circuit breakers protect external APIs (2Captcha, Gmail, Outlook) from repeated failed calls.

### Email feedback loop

When `python -m job_agent.cli email` detects a recruiter reply, it updates the job status to `responded` and writes an outcome record to `data/feedback_ledger.json`. Outcomes are also recorded for `failed` (rejection-like) and `needs_human` results.

`job_application_system/agents/resume_tailor.py` and `tailor_bridge.py` read the ledger and feed successful claims into future resume tailoring as hints. Over time, phrasing and claims that produced callbacks receive higher weight, improving the ATS / Recruiter Scoring Agent's output.

### Structured logging

- `LOG_LEVEL=INFO` or `DEBUG`.
- `LOG_TO_FILE=true` writes to `AGENT_LOG_FILE`.
- `JSON_LOGS=true` emits JSON lines for ingestion into a log aggregator.

## Scrapling service & Docker

The discovery layer and all Cloudflare-protected browser work run inside a dedicated Docker container so the host machine does not need to install browser binaries or stealth dependencies.

### Local development (no Docker)

By default `SCRAPLING_USE_SERVICE=false`, so the local `job_agent` code falls back to plain HTTP requests for simple API-based sources (Greenhouse, Lever, Indeed RSS, etc.). This keeps unit tests fast and avoids pulling multi-gigabyte browser images during development.

If you want to exercise the Scrapling service locally:

```bash
scripts/run_scrapling_service.sh      # Unix
scripts/run_scrapling_service.ps1     # Windows
```

The service starts on `http://localhost:8723` and exposes:

- `GET /health` — health check.
- `POST /fetch` — fast static HTTP fetch with `impersonate='chrome'`.
- `POST /stealth-fetch` — real browser fetch with Cloudflare challenge solving.
- `POST /dynamic-fetch` — dynamic JavaScript rendering.
- `POST /spider/run` — generic job-discovery spider with pause/resume checkpointing.
- `POST /select` — adaptive CSS/XPath extraction.
- `POST /extension/snapshot` — endpoint used by the Chrome extension to store unknown ATS DOM snapshots.
- `POST /submit/cloudflare` — stealth page snapshot for submission flows behind Cloudflare.

### Docker Compose

```bash
docker compose up --build
```

This starts two containers:

- `job-agent-scrapling` — the Scrapling service (`scrapling_service/`).
- `job-agent-main` — the main orchestrator, which points `SCRAPLING_SERVICE_URL=http://scrapling-service:8723` and `SCRAPLING_USE_SERVICE=true`.

Only three host directories are mounted into the containers: `data/`, `logs/`, and `resume/` (plus `base resume/` and `base cover letter/` for the main container). All browser binaries, fingerprints, and Scrapling checkpoints live inside the container image.

### Environment variables for Scrapling

```env
SCRAPLING_SERVICE_URL=http://localhost:8723
SCRAPLING_USE_SERVICE=false   # set true inside Docker or when running the service
PROXY_LIST=...                # passed to Scrapling fetchers and spiders
```

## CLI commands

```bash
python -m job_agent.cli --help
python -m job_agent.cli intake                           # interactive profile wizard
python -m job_agent.cli show-config
python -m job_agent.cli discover --sources greenhouse,lever
python -m job_agent.cli pipeline --sources greenhouse    # discover -> score -> tailor -> submit (dry-run)
python -m job_agent.cli run --jobs data/jobs.json --dry-run
python -m job_agent.cli run --jobs data/jobs.json        # respects ENABLE_AUTO_SUBMIT
python -m job_agent.cli email                            # check all enabled inboxes for recruiter updates
python -m job_agent.cli drafts                           # create draft replies for recruiter emails
python -m job_agent.cli sync                             # push Excel + resumes to Google
python -m job_agent.cli schedule --sources greenhouse --time 09:00 --dry-run  # preview daily task
python -m job_agent.cli schedule --sources greenhouse --time 09:00             # Windows Task Scheduler / cron
python -m job_agent.cli unschedule                                             # remove scheduled task
python -m job_agent.cli generate-adapter --snapshot data/snapshot.json         # generate SiteAdapter draft from Chrome extension
python -m job_agent.cli approve-adapter --platform oraclecloud               # enable draft for autonomous use
python -m job_agent.cli reject-adapter --platform oraclecloud                # discard draft
```

To compare Scrapling success rates and timings against the fallback path, use the benchmark script:

```bash
python scripts/benchmark_discovery.py --urls https://boards.greenhouse.io/gradial --spider
```

## Google integration

1. Create a Google Cloud service account and download the JSON key.
2. In `.env`:
   - `GOOGLE_SERVICE_ACCOUNT_JSON=secrets/service-account.json`
   - `GOOGLE_SHEET_ID=...`
   - `GOOGLE_DRIVE_FOLDER_ID=...`
3. Share the Google Sheet and Drive folder with the service-account email.
4. Run `python -m job_agent.cli sync`.

## Gmail integration

1. Enable the Gmail API in Google Cloud and download OAuth credentials.
2. In `.env`:
   - `GMAIL_CREDENTIALS_JSON=secrets/gmail-credentials.json`
   - `GMAIL_SENDER_EMAIL=your.email@example.com`
3. Run `python -m job_agent.cli email` or `python -m job_agent.cli drafts`.

The first run will open a browser for OAuth consent.

## Outlook / Microsoft 365 integration

1. Register an app in Azure AD: https://portal.azure.com/#blade/Microsoft_AAD_RegisteredApps/ApplicationsListBlade
2. Add **delegated** Microsoft Graph permissions:
   - `Mail.Read` — read inbox
   - `Mail.ReadWrite` — create drafts
   - `Mail.Send` — send emails (if you later enable direct send)
3. Copy the **Application (client) ID** to `.env`:
   - `OUTLOOK_CLIENT_ID=your-client-id`
4. Set `OUTLOOK_USE_DEVICE_CODE=true` to authenticate on any device, or `false` for an interactive browser window.
5. Run `python -m job_agent.cli email` to scan for recruiter updates or `python -m job_agent.cli drafts` to create reviewable draft replies.

The agent never sends Outlook emails by default; it only creates drafts for you to review and send.

## Email draft reply scenarios

When you run `python -m job_agent.cli drafts`, the agent scans the last 7 days of enabled inboxes and creates drafts for recruiter emails matching submitted jobs. It chooses a human-tone template based on the message:

- **availability** — interview/phone-screen scheduling requests
- **thank_you** — post-interview thank-you note
- **follow_up** — status check / follow-up
- **general** — any other recruiter update

All drafts are saved in your Outlook/Gmail drafts folder for review before sending.

## Adding a new site adapter

Implement `SiteAdapter` from `job_agent/sites/base.py` and register it in `job_agent/sites/registry.py`:

```python
from job_agent.sites.base import SiteAdapter
from job_agent.sites.registry import build_default_registry

class MyCompanyAdapter(SiteAdapter):
    platform = "mycompany"

    @classmethod
    def can_handle(cls, url: str) -> bool:
        return "mycompany.com/careers" in url

    def platform_name(self) -> str:
        return self.platform

    async def is_login_required(self, page: Page) -> bool:
        ...

    async def authenticate(self, page, account, create_account=False) -> bool:
        ...

    async def parse_form(self, page: Page) -> dict:
        ...

    async def fill_application(self, page, job, resume_path, profile, dry_run=False):
        ...

    async def submit(self, page, dry_run) -> bool:
        ...

    async def detect_challenges(self, page, dry_run=False) -> None:
        ...


registry = build_default_registry()
registry.register(MyCompanyAdapter)
```

## Learning new ATS platforms (Chrome extension + Adapter Generator)

Not every ATS uses Greenhouse, Workday, or iCIMS. When the Submission Agent lands on an unknown portal (e.g., Oracle Cloud HCM, SAP SuccessFactors, a custom Taleo flow), it saves the job as `needs_human` and prompts you to capture the page with the Chrome extension.

### How the learning loop works

1. Install the extension from `chrome_extension/` in developer mode (Chrome → Extensions → Load unpacked).
2. Open a job posting or application form on the unknown ATS.
3. Click **Capture ATS Page** in the popup.
   - The extension extracts the full rendered DOM.
   - It extracts every visible form field (`input`, `select`, `textarea`) with labels, types, options, and required flags.
   - It POSTs the snapshot to `http://localhost:8723/extension/snapshot` (the Scrapling service), which writes it to `data/adapter_drafts/<platform>/<snapshot>.json`.
4. Generate a draft adapter locally:

   ```bash
   python -m job_agent.cli generate-adapter --snapshot data/adapter_drafts/oraclecloud/snapshot_20260801_043926.json
   ```

   The Adapter Generator Agent (heuristic + LLM) reads the snapshot and produces a `SiteAdapter` Python class that implements the `job_agent.sites.base` protocol.
5. Review the generated Python file in `data/adapter_drafts/<platform>/adapter.py`.
6. Approve it so the registry can load it automatically:

   ```bash
   python -m job_agent.cli approve-adapter --platform oraclecloud
   ```

   Approved adapters are copied to `job_agent/sites/generated_drafts/` and loaded by `job_agent/sites/registry.py` on the next run.
7. Reject bad drafts:

   ```bash
   python -m job_agent.cli reject-adapter --platform oraclecloud
   ```

This creates a closed learning loop: the first encounter with a new platform is human-assisted, but every subsequent posting on that platform is handled autonomously.

## Running tests

```bash
source .venv/Scripts/activate
pytest tests/ -v
```

## Status values

- `pending` — waiting to be processed
- `queued` — dry-run succeeded, ready for real submission
- `in_progress` — currently being filled
- `submitted` — real application sent
- `failed` — submission error, will retry if budget remains
- `duplicate` — skipped because already applied
- `needs_human` — CAPTCHA, login wall, or unsupported form
- `responded` — recruiter reply detected via email

## TODO / next steps

- Build the Scrapling service image and verify `docker compose up --build` starts both containers cleanly.
- Run the benchmark script against real job-board URLs (LinkedIn, Indeed, Greenhouse, Workday, iCIMS, and 5+ company career pages) to confirm success-rate and timing improvements.
- Test the Chrome extension on an unknown ATS (Oracle Cloud HCM, SAP SuccessFactors, Taleo) and validate one full generate-adapter → approve-adapter → auto-submit cycle.
- Integrate `scrapling_mixin.py` into `ApplicationSubmissionAgent` so Cloudflare-protected submission flows use `StealthySession` automatically.
- Runtime-test image/grid CAPTCHA paths beyond reCAPTCHA/hCaptcha via the 2Captcha fallback.
- Migrate credential master key to OS keyring or cloud secret manager for production.
- Add monitoring for the Scrapling service health and per-domain circuit-breaker state.

## Security note

Credentials are encrypted at rest in the local SQLite database. The Fernet master key is read from `CREDENTIAL_MASTER_KEY` or a local key file. Do not commit `.env`, `.credential_key`, or the SQLite database to git. For production, use a proper secret manager (e.g., Azure Key Vault, AWS Secrets Manager, or OS keyring) instead of a plain env var or key file.
