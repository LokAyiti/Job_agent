# Automated Job Application System — Track A

Track A: Scrape jobs, analyze JDs, tailor resumes, generate cover letters, and log everything to Excel.

## Setup

1. Copy `sample.env` to `.env` and fill in credentials.
2. Install dependencies in the project virtual environment:

```bash
source /c/Job_agent/.venv/Scripts/activate
pip install -r requirements.txt
```

3. Run Track A:

```bash
# Test with 1 state, 1 page, default Data Analyst filter
python main.py --states 1 --pages-per-state 1

# Production: all 50 states, 2 pages per state
python main.py --states 50 --pages-per-state 2 --title-filter "data analyst"
```

## Outputs

- `resume/` — tailored PDF/DOCX resumes named `JD_<Role>_Lokesh_<job_id>_<date>.pdf`
- `cover_letter/` — tailored PDF/DOCX cover letters
- `data/jobs_log.xlsx` — Excel log of all processed jobs
- `data/<job_id>.json` — per-job raw data for Track B (application submission)

## LLM providers

Primary: **Databricks** (`databricks-claude-sonnet-4-6`)
Fallback: **OpenRouter** (configured but kept as a backup)

## Safety

Track A does not submit applications. When Track B is implemented, `REQUIRES_APPROVAL=true` in `.env` will block any automatic submission unless explicitly disabled.

## Next steps (Track B)

- Add login + application submission agent
- Add email/recruiter communication agent
- Add Google Drive/Sheets sync
- Add CAPTCHA handling (2captcha + human-in-the-loop)
- Add duplicate prevention and rate-limiting
