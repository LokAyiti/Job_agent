#!/usr/bin/env bash
# Run the Scrapling service locally for development (without Docker).
# Requires the project's virtual environment to be activated and scrapling installed.
set -euo pipefail

SCRAPLING_SERVICE_URL="${SCRAPLING_SERVICE_URL:-http://localhost:8723}"
ADAPTER_DRAFTS_DIR="${ADAPTER_DRAFTS_DIR:-./data/adapter_drafts}"

cd "$(dirname "$0")/.."

mkdir -p data adapter_drafts logs resume crawl_data

export ADAPTER_DRAFTS_DIR
exec python -m uvicorn scrapling_service.main:app --host 0.0.0.0 --port 8723 "$@"
