# Run the Scrapling service locally for development (without Docker).
# Requires the project's virtual environment to be activated and scrapling installed.
$env:SCRAPLING_SERVICE_URL = $env:SCRAPLING_SERVICE_URL -or "http://localhost:8723"
$env:ADAPTER_DRAFTS_DIR = $env:ADAPTER_DRAFTS_DIR -or "./data/adapter_drafts"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location (Join-Path $scriptDir "..")

New-Item -ItemType Directory -Force -Path data, logs, resume, crawl_data | Out-Null

python -m uvicorn scrapling_service.main:app --host 0.0.0.0 --port 8723 @args
