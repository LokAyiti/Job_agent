# Automated Job Application System

End-to-end multi-agent pipeline that discovers jobs, scores fit, tailors resumes, submits applications, and monitors recruiter replies.

## Tracks

- **User Intake** — interactive CLI wizard that produces a unified `profile.json`.
- **Track C — Job Discovery Agent** — scrapes Greenhouse, Lever, GovernmentJobs, LinkedIn (guest API), Indeed (RSS), and configured company career pages. LinkedIn/Indeed are disabled by default and must be opted in via `profile.json`.
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
    greenhouse.py         # Greenhouse API discovery
    lever.py              # Lever API discovery
    governmentjobs.py     # GovernmentJobs.com scraper
    company_pages.py      # Generic career-page crawler
    linkedin.py           # LinkedIn scaffolding (disabled by default)
    indeed.py             # Indeed scaffolding (disabled by default)
  persistence/
    credentials.py        # Encrypted SQLite account store
    excel_logger.py       # applications.xlsx log
    sqlite_queue.py       # Crash-recovery queue
    google_sync.py        # Google Drive/Sheets sync
  sites/
    base.py               # SiteAdapter protocol
    registry.py           # Central adapter registry
    greenhouse.py         # Greenhouse adapter
    workday.py            # Workday adapter
    icims.py              # iCIMS adapter
  utils/
    encryption.py         # Fernet credential vault
    proxy_rotator.py     # Proxy rotation
    humanizer.py          # Stealth scripts and human-like pacing
    circuit_breaker.py    # Circuit breaker + retry helpers
    structured_logging.py # loguru configuration
  captcha.py              # 2Captcha integration
  config.py               # pydantic-settings + .env
  models.py               # JobApplication, Resume, Account, statuses
  cli.py                  # Command-line entry

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
- `OPENROUTER_API_KEY` / `DATABRICKS_TOKEN` + `DATABRICKS_SONNET_ENDPOINT` — LLM providers for scoring and resume tailoring. OpenRouter is preferred; Databricks is used as a runtime fallback.
- Google service-account JSON path + sheet/drive IDs
- Gmail credentials JSON + sender email

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
2. **Track C — Job Discovery** fetches postings from Greenhouse, Lever, GovernmentJobs, LinkedIn, Indeed, and any configured company career pages. LinkedIn/Indeed are disabled by default and can be enabled in `profile.json`.
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
- Each browser context uses a rotating realistic desktop user agent (`fake-useragent`) and `playwright-stealth` anti-detection scripts to reduce bot fingerprints.

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

Set a comma-separated proxy list:

```env
PROXY_LIST=http://proxy1.example.com:8080,user:pass@proxy2.example.com:8080
```

The Submission Agent assigns proxies **deterministically per domain** (`job_agent.utils.proxy_rotator.get_for_domain`) so the same platform always uses the same proxy across sessions. This keeps account login sessions stable on ATS portals like Workday and iCIMS. A random proxy is still used when no domain is known. Leave blank to run without a proxy.

### Stealth browsing and human pacing

- `USE_STEALTH=true` injects anti-detection scripts into each page.
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

## Chrome extension for complex flows

If a site is heavily JavaScript-driven, uses a login wall, or has a multi-step wizard that Playwright cannot capture reliably, the adapter will set the job status to `needs_human`. A future Chrome extension can assist by:

1. Reading the full page DOM from the user's real browser session.
2. Exposing a `/fill-step` or `/submit-step` API that the Orchestrator calls.
3. Falling back to the user approving each step before the agent clicks.

The architecture is already split so the extension can be added as a new `SiteAdapter` or a remote browser bridge without changing the Orchestrator.

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

- Refine selectors per real company page for Greenhouse, Workday, and iCIMS.
- Add a Chrome extension adapter for heavily JavaScript-driven or login-walled application flows.
- Migrate credential master key to OS keyring or cloud secret manager for production.

## Security note

Credentials are encrypted at rest in the local SQLite database. The Fernet master key is read from `CREDENTIAL_MASTER_KEY` or a local key file. Do not commit `.env`, `.credential_key`, or the SQLite database to git. For production, use a proper secret manager (e.g., Azure Key Vault, AWS Secrets Manager, or OS keyring) instead of a plain env var or key file.
