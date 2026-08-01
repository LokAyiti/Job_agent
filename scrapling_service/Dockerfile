# Scrapling service for the Job Agent.
# Runs inside the official Scrapling Docker image so the host does not need
# browser binaries or anti-bot dependencies.
FROM ghcr.io/d4vinci/scrapling:latest

WORKDIR /app

# Install the tiny HTTP service layer on top of the pre-baked Scrapling image.
COPY scrapling_service/requirements.txt /app/scrapling_service/requirements.txt
RUN pip install --no-cache-dir -r /app/scrapling_service/requirements.txt

# Make sure all Scrapling browsers and fingerprint-spoofing deps are present.
RUN scrapling install

COPY scrapling_service/ /app/scrapling_service/

EXPOSE 8723

CMD ["python", "-m", "uvicorn", "scrapling_service.main:app", "--host", "0.0.0.0", "--port", "8723"]
