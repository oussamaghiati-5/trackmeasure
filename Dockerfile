# Base image version must equal the pinned playwright version in requirements.txt.
# Check available tags: https://mcr.microsoft.com/v2/playwright/python/tags/list
# This image ships Python 3.12 (Ubuntu 24.04 Noble).
# If Python 3.13 is strictly required, pin a tag ending in -python-3.13-noble.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

# xvfb: for headful runs inside the container (xvfb-run -a python crawler.py ... --headful)
RUN apt-get update && \
    apt-get install -y --no-install-recommends xvfb && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps as root so they land in the system site-packages
# visible to pwuser. The base image already has playwright 1.63.0; pip will
# confirm the version rather than reinstall.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Match pwuser to the host user so the bind-mounted data/ and results/ are writable
# (the Makefile passes `id -u`/`id -g`; -o allows a uid already used in the image).
ARG PW_UID=1001
ARG PW_GID=1001
RUN if [ "$(id -u pwuser)" != "$PW_UID" ] || [ "$(id -g pwuser)" != "$PW_GID" ]; then \
      groupmod -o -g "$PW_GID" pwuser && usermod -o -u "$PW_UID" -g "$PW_GID" pwuser; \
    fi

# Copy source (data/ and ext/ are mounted at runtime; excluded via .dockerignore)
# --chown: Chromium must be able to read (and write generated rulesets into) unpacked extensions.
COPY --chown=pwuser:pwuser . /app

# Run as the non-root user pre-created by the base image (uid set above).
# This avoids the --no-sandbox flag that root would force on Chromium.
USER pwuser

CMD ["python", "--version"]
