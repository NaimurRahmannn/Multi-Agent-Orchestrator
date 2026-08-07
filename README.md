# Multi-Agent Orchestrator

Multi-Agent Orchestrator is the repository wrapper for **AgentOrchestra**, a local multi-agent webpage editor for a committed static sample site. The application routes natural-language edit requests to specialist agents, validates proposed changes, and promotes only accepted work into the working site.

The main project lives in [`agentorchestra/`](agentorchestra/).

## Repository layout

```text
.
├── Dockerfile
├── docker-compose.yml
├── package.json
└── agentorchestra/
    ├── README.md
    ├── pyproject.toml
    ├── docs/
    ├── scripts/
    ├── sites/
    ├── src/
    └── tests/
```

## Requirements

- Python `>=3.10,<3.14`
- [uv](https://docs.astral.sh/uv/)
- Node.js and npm
- Playwright Chromium
- Groq API keys and models for the live Manager, HTML, CSS, SEO, and QA roles

## Quick start

From the repository root:

```bash
cd agentorchestra
uv sync --frozen
npm ci
uv run playwright install chromium
cp .env.example .env
```

Edit `agentorchestra/.env` with your Groq credentials, then verify and launch:

```bash
uv run python scripts/run_demo.py --check
uv run python scripts/reset_demo_site.py --reset
uv run python scripts/run_ui.py
```

The Streamlit UI starts on port `8501` by default.

## Docker

From the repository root:

```bash
docker build -t agentorchestra -f Dockerfile .
docker compose up --build app
```

The container uses `agentorchestra/.env` and serves the UI on `http://localhost:8501`.

To run the clean-install verifier inside Docker:

```bash
docker compose --profile verify run --rm verify
```

## Testing

From `agentorchestra/`:

```bash
uv run python -m pytest -q
uv run ruff check .
git diff --check
uv run python scripts/verify_clean_install.py --check
```

## Documentation

- [`agentorchestra/README.md`](agentorchestra/README.md)
- [`agentorchestra/docs/setup.md`](agentorchestra/docs/setup.md)
- [`agentorchestra/docs/usage.md`](agentorchestra/docs/usage.md)
- [`agentorchestra/docs/architecture.md`](agentorchestra/docs/architecture.md)
- [`agentorchestra/docs/demo.md`](agentorchestra/docs/demo.md)
- [`agentorchestra/docs/troubleshooting.md`](agentorchestra/docs/troubleshooting.md)
