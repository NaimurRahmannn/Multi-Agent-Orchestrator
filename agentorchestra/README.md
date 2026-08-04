# AgentOrchestra

AgentOrchestra is a phase-by-phase CrewAI evaluation project for routing natural-language requests to specialist agents that edit a small static HTML/CSS website.

The central architecture rule is: **Manager decides what should run. Flow decides what actually runs.**

## Phase 1 Status

Phase 1 implements project foundation and feasibility only:

- Typed environment configuration for Groq-backed operations.
- Strict Pydantic routing models for Manager output.
- A fixed local static sample site in `sites/fixture` with an identical starting copy in `sites/working`.
- Manual feasibility checks for CrewAI imports, Groq connectivity, structured Manager output, Lighthouse SEO-only execution, and Playwright Chromium screenshots.
- Deterministic tests that do not call Groq, Lighthouse, or browser automation.

Production Manager, HTML, CSS, SEO, QA agents, the staged editing pipeline, full Flow orchestration, patch tools, and Streamlit UI are not implemented yet.

## Prerequisites

- Python `>=3.10,<3.14` with the existing project virtual environment.
- Node.js, npm, and npx.
- Local Chromium installed for Playwright.
- Lighthouse installed through this app root's Node dependencies.

## Setup

Do not recreate the virtual environment if it already exists. From this `agentorchestra` directory:

```bash
cp .env.example .env
```

Fill in `.env` only when running live Groq checks:

```text
GROQ_API_KEY=
GROQ_MODEL=
APP_ENV=development
LOG_LEVEL=INFO
```

Install Python dependencies with the project toolchain:

```bash
crewai install
```

Install Node dependencies for Lighthouse from this app root:

```bash
npm install
```

Install Playwright Chromium:

```bash
playwright install chromium
```

## Feasibility Checks

Run these from the `agentorchestra` directory.

```bash
python scripts/feasibility/check_environment.py
python scripts/feasibility/check_groq.py
python scripts/feasibility/check_manager_output.py
python scripts/feasibility/check_lighthouse.py
python scripts/feasibility/check_playwright.py
```

Live Groq checks consume a small number of tokens. Automated tests do not use real API tokens.

Expected generated outputs:

- `reports/routing/phase1_manager_trials.json`
- `reports/lighthouse/phase1-seo.json`
- `reports/screenshots/phase1-index.png`

## Tests And Linting

```bash
pytest -q
pytest --cov=agentorchestra --cov-report=term-missing
ruff check .
```

## Sample Site

The initial static site lives in `sites/fixture`. `sites/working` starts as an identical copy and will become the accepted editable version in later phases. `sites/staging` is intentionally empty except for `.gitkeep`.

The site has no JavaScript, remote fonts, CDNs, remote images, or backend behavior.
