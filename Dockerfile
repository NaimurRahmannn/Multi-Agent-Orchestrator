FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    AGENTORCHESTRA_ROOT=/workspace/agentorchestra \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    chromium \
    curl \
    git \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install uv

WORKDIR /workspace

COPY package.json package-lock.json ./
COPY agentorchestra/package.json agentorchestra/package-lock.json ./agentorchestra/
COPY agentorchestra/pyproject.toml agentorchestra/uv.lock ./agentorchestra/
COPY agentorchestra/src ./agentorchestra/src

RUN npm ci
RUN npm ci --prefix /workspace/agentorchestra
RUN uv sync --frozen --no-dev --project /workspace/agentorchestra
RUN uv run --project /workspace/agentorchestra playwright install chromium

COPY agentorchestra/ ./agentorchestra/

WORKDIR /workspace/agentorchestra

EXPOSE 8501

CMD ["uv", "run", "python", "scripts/run_ui.py"]