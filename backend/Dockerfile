# MimicUser API and research worker — one image, two commands.
#
# The worker drives a real Chromium through Playwright, and the Claude Agent SDK
# ships its own Claude Code CLI binary, so the image carries both.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Dependencies first, read straight from pyproject, so source edits keep this
# layer. The app runs from /app itself: pyproject's package list omits
# `services` and `personas`, so an installed copy would be incomplete.
COPY pyproject.toml ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > /tmp/requirements.txt \
 && pip install -r /tmp/requirements.txt \
 && python -m playwright install --with-deps chromium

RUN groupadd --system mimic \
 && useradd --system --gid mimic --create-home --home-dir /home/mimic mimic

COPY . .
RUN mkdir -p /data/storage /data/runs && chown -R mimic:mimic /app /data

USER mimic
ENV STORAGE_ROOT=/data/storage \
    WORKDIR=/data/runs

EXPOSE 8000
# The published port binds to 127.0.0.1 on the host, so only the reverse proxy
# can reach this listener and supply forwarded headers.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
