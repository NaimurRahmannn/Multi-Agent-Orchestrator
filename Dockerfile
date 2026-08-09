FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    AGENTORCHESTRA_ROOT=/workspace/agentorchestra \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install uv

WORKDIR /workspace

COPY agentorchestra/package.json agentorchestra/package-lock.json ./agentorchestra/
COPY agentorchestra/pyproject.toml agentorchestra/uv.lock ./agentorchestra/
COPY agentorchestra/src ./agentorchestra/src

RUN npm ci --omit=dev --prefix /workspace/agentorchestra
RUN uv sync --frozen --no-dev --project /workspace/agentorchestra
RUN uv run --project /workspace/agentorchestra playwright install --with-deps chromium \
    && PLAYWRIGHT_CHROMIUM="$(find "${PLAYWRIGHT_BROWSERS_PATH}" -type f -name chrome -print -quit)" \
    && test -n "${PLAYWRIGHT_CHROMIUM}" \
    && ln -s "${PLAYWRIGHT_CHROMIUM}" /usr/local/bin/playwright-chromium \
    && chmod -R a+rX "${PLAYWRIGHT_BROWSERS_PATH}" \
    && rm -rf /var/lib/apt/lists/*

ENV CHROME_PATH=/usr/local/bin/playwright-chromium

ARG UID=1000
ARG GID=1000
RUN groupadd -g ${GID} appuser \
    && useradd -m -u ${UID} -g ${GID} appuser

COPY --chown=appuser:appuser agentorchestra/ ./agentorchestra/

WORKDIR /workspace/agentorchestra

USER appuser

EXPOSE 8501

CMD ["uv", "run", "--no-sync", "python", "scripts/run_ui.py"]
