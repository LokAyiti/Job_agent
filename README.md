# Automated Job Application System — Track B

Track B implements the **Action & Persistence Layer** of the multi-agent job application system.

It includes:
- **Application Submission Agent** (Playwright + site adapters)
- **Email/Recruiter Communication Agent** (Gmail API)
- **Orchestrator / CEO Agent** (sequencing, retries, logging)
- **Excel logger** and **SQLite job queue** for state
- **Google Drive/Sheets sync** (optional)
- **2Captcha CAPTCHA solving** with human-in-the-loop fallback
- **Credential store** for platform accounts (Workday, iCIMS, etc.)

Track B consumes tailored resumes produced by Track A from the `resume/` folder.

## Project layout

```
job_agent/
  agents/
    base_agent.py         # Shared utilities
    submission_agent.py   # Fills/submits applications, solves CAPTCHA, manages login
    email_agent.py        # Gmail monitor & responses
    orchestrator.py       # CEO agent
  persistence/
    credentials.py        # Local SQLite account store (plaintext in this phase)
    excel_logger.py       # applications.xlsx log
    sqlite_queue.py       # Crash-recovery queue
    google_sync.py        # Google Drive/Sheets sync
  sites/
    base.py               # SiteAdapter protocol
    registry.py           # Central adapter registry
    greenhouse.py         # Greenhouse adapter
    workday.py            # Workday adapter
    icims.py              # iCIMS adapter
  captcha.py              # 2Captcha integration
  config.py               # pydantic-settings + .env
  models.py               # JobApplication, Resume, Account, statuses
  cli.py                  # Command-line entry
```

## Quick start

1. Activate the virtual environment (already created):

```bash
source .venv/Scripts/activate  # Windows Git Bash
# or .venv\Scripts\activate    # Windows Command Prompt
```

2. Copy `.env.example` to `.env` and fill in your real values:

```bash
cp .env.example .env
```

Required for any run:
- `MY_NAME`, `MY_EMAIL`, `MY_PHONE`, `MY_LINKEDIN`
- `RESUME_DIR` (default `resume/` is fine)
- `LOG_FILE` (default `logs/applications.xlsx` is fine)

Optional:
- `LOGIN_EMAIL` + `LOGIN_PASSWORD` — used to create candidate accounts on platforms that require sign-up (Workday, iCIMS, etc.). Stored locally in plaintext in this starting phase.
- `TWOCAPTCHA_API_KEY` — automatic CAPTCHA solving; leave blank to rely on human-in-the-loop.
- Google service-account JSON path + sheet/drive IDs
- Gmail credentials JSON + sender email

3. Place one or more tailored resumes in `resume/` using the naming convention:

```
resume/JD_Role_MyName_TodaysDate.pdf
```

4. Create a job list and run in dry-run mode (default):

```bash
python -m job_agent.cli create-sample-jobs
python -m job_agent.cli run --jobs data/sample_jobs.json --dry-run
```

5. Check the Excel log:

```bash
python -m job_agent.cli show-config
```

The log is at `logs/applications.xlsx`.

## Safety switches & anti-detection

- `ENABLE_AUTO_SUBMIT=false` (default) makes the Submission Agent stop at the final submit button and log the job as `queued` instead of actually submitting. **Keep this false until you are ready to apply for real.**
- `HUMAN_IN_THE_LOOP=true` pauses and flags jobs as `needs_human` when CAPTCHA cannot be solved automatically, login/account creation fails, or an unsupported form is detected.
- `MAX_RETRIES`, `RETRY_DELAY_SECONDS`, and `DELAY_BETWEEN_JOBS_SECONDS` control rate limiting and retries.
- Each browser context uses a realistic user agent and viewport to reduce bot detection.

## Platform-specific adapters

The Submission Agent picks an adapter based on the job URL:

- `greenhouse` — `boards.greenhouse.io` public application forms.
- `workday` — `myworkdayjobs.com` portals (account creation/login, multi-step wizard aware).
- `icims` — `icims.com` / `applicantpro.com` portals (account creation/login, iframe aware).

For platforms that require a candidate account, the Orchestrator checks the local credential store first. If no account exists, it creates one with `LOGIN_EMAIL`/`LOGIN_PASSWORD` and stores the credentials for reuse.

## CAPTCHA handling

1. If `TWOCAPTCHA_API_KEY` is set, the Submission Agent sends reCAPTCHA/hCaptcha challenges to 2Captcha and injects the returned token.
2. If 2Captcha fails, is not configured, or an unsupported challenge type appears, the agent pauses for manual entry (`HUMAN_IN_THE_LOOP=true`) or flags the job as `needs_human`.

## CLI commands

```bash
python -m job_agent.cli --help
python -m job_agent.cli show-config
python -m job_agent.cli create-sample-jobs
python -m job_agent.cli run --jobs data/jobs.json --dry-run
python -m job_agent.cli run --jobs data/jobs.json        # respects ENABLE_AUTO_SUBMIT
python -m job_agent.cli email                            # check all enabled inboxes for recruiter updates
python -m job_agent.cli drafts                           # create draft replies for recruiter emails
python -m job_agent.cli sync                             # push Excel + resumes to Google
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
- Add a human-approval gate before real submissions.
- Encrypt stored credentials (keyring or local master key) before production use.
- Add more site adapters (Lever, LinkedIn Easy Apply, custom company portals).
- Integrate Track A resume generation so resumes are produced automatically.
- Add a Chrome extension adapter for JS-heavy portals.
- Add CAPTCHA handling for image/grid challenges not covered by 2Captcha.

## Security note

Account credentials are stored locally in plaintext SQLite in this starting phase. Do not commit `.env` or the SQLite database to git. Replace the credential store with encryption or a system credential store before running against production accounts with sensitive passwords.
