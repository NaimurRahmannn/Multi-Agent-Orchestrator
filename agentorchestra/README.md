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

## Phase 2 Status

Phase 2 adds the production configuration and strict domain contracts future phases will use:

- `EditRequest` validates safe single-page static HTML edit requests.
- `ManagerRoutingPlan` validates `execute`, `clarification_required`, and `out_of_scope` routing plans.
- `PatchProposal` validates specialist patch proposals but does not edit files.
- `RoutingEvidenceCase`, `RoutingEvidenceResult`, and `TokenUsage` define routing benchmark evidence.
- `QAResult` and `CriterionResult` validate QA verdict structure but do not execute QA.
- `validate_qa_coverage()` checks QA criterion coverage against Manager acceptance criteria.

Supported routing statuses are `execute`, `clarification_required`, and `out_of_scope`. Supported specialist names are `html`, `css`, and `seo`; QA is intentionally not a selectable specialist.

These models are contracts only. Production agents, production Flow orchestration, staged patch execution, production Lighthouse services, production Playwright services, and the Streamlit UI are still future-phase work.

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
AGENTORCHESTRA_ROOT=
```

Configuration is loaded with `pydantic-settings`. `AGENTORCHESTRA_ROOT` is optional and is mainly useful for tests or unusual local layouts; derived site and report paths remain under that root. Runtime-directory creation is explicit and limited to staging and report directories.

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

Focused contract suites:

```bash
pytest tests/test_config.py -q
pytest tests/test_models.py -q
pytest tests/test_edit_request.py -q
pytest tests/test_patch_proposal.py -q
pytest tests/test_routing_evidence_models.py -q
pytest tests/test_qa_models.py -q
```

## Sample Site

The initial static site lives in `sites/fixture`. `sites/working` starts as an identical copy and will become the accepted editable version in later phases. `sites/staging` is intentionally empty except for `.gitkeep`.

The site has no JavaScript, remote fonts, CDNs, remote images, or backend behavior.
