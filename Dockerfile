# Build context for the main job-agent container.
# This container runs the orchestrator, submission agent, email agent, and
# resume-tailoring bridge. It does NOT need browser binaries; it talks to the
# scrapling-service container for anti-bot rendering and spider crawling.
FROM python:3.12-slim

WORKDIR /app

# Install git so pip can install dependencies from VCS if needed.
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app/

# Default command: keep container alive so users can exec commands.
CMD ["tail", "-f", "/dev/null"]
