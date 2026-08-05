# AgentOrchestra

AgentOrchestra is an incremental CrewAI evaluation project for routing natural-language requests to specialist agents that edit a small static HTML/CSS website.

The central architecture rule is: **Manager decides what should run. Flow decides what actually runs.**

## Foundation Status

The foundation work implements project setup and feasibility only:

- Typed environment configuration for Groq-backed operations.
- Strict Pydantic routing models for Manager output.
- A fixed local static sample site in `sites/fixture` with an identical starting copy in `sites/working`.
- Manual feasibility checks for CrewAI imports, Groq connectivity, structured Manager output, Lighthouse SEO-only execution, and Playwright Chromium screenshots.
- Deterministic tests that do not call Groq, Lighthouse, or browser automation.

Production Manager routing, staged workspace tooling, and HTML/CSS specialist previews are implemented incrementally. SEO execution, QA execution, promotion, the complete Flow, and Streamlit UI are not implemented yet.

## Domain Contracts Status

The domain-contract work adds the production configuration and strict models future work will use:

- `EditRequest` validates safe single-page static HTML edit requests.
- `ManagerRoutingPlan` validates `execute`, `clarification_required`, and `out_of_scope` routing plans.
- `PatchProposal` validates specialist patch proposals but does not edit files.
- `RoutingEvidenceCase`, `RoutingEvidenceResult`, and `TokenUsage` define routing benchmark evidence.
- `QAResult` and `CriterionResult` validate QA verdict structure but do not execute QA.
- `validate_qa_coverage()` checks QA criterion coverage against Manager acceptance criteria.

Supported routing statuses are `execute`, `clarification_required`, and `out_of_scope`. Supported specialist names are `html`, `css`, and `seo`; QA is intentionally not a selectable specialist.

These models are contracts only. Production agents, production Flow orchestration, production Lighthouse services, production Playwright services, and the Streamlit UI are still future work.

## Manager Routing Status

The Manager routing work adds the production Manager routing layer and benchmark:

- A real CrewAI Manager Agent with no tools, no delegation, no memory, and one structured routing task.
- `ManagerRouter`, which converts an `EditRequest` into a validated `ManagerRoutingPlan`.
- `ManagerRunResult`, which records the request, plan, model identifier, monotonic latency, and available token usage.
- A typed routing evidence bank with eight approved benchmark cases and separate optional diagnostic cases.
- `RoutingBenchmarkRunner`, which evaluates routing correctness, continues after individual failures, and writes deterministic JSON reports.
- Thin CLI commands for one live Manager request and the approved live benchmark.

The architecture remains:

```text
EditRequest
  -> ManagerRouter
  -> CrewAI Manager Agent + one routing Task
  -> structured CrewAI output
  -> ManagerRoutingPlan validation
  -> ManagerRunResult / routing evidence
```

**Manager decides what should run. Flow decides what actually runs.**

The Manager only plans. It does not read files, edit HTML/CSS, run Lighthouse, run Playwright, create staging, invoke specialists, run QA, or promote changes.

### Manager Ownership Boundaries

- HTML owns broken structure, elements, attributes, explicit alt text, labels, semantic markup, broken heading markup, and narrow structural changes.
- CSS owns colors, typography, visual heading size, spacing, borders, layout, and responsive presentation.
- SEO owns narrow on-page SEO work: page titles, meta descriptions, SEO-focused heading hierarchy, requested Open Graph metadata, and weak SEO diagnosis.
- Ambiguous requests, such as `Make it better`, require clarification and select no specialists.
- Backend, JavaScript, database, framework, upload, deployment, live-site editing, and full accessibility certification requests are out of scope.

### Manager Commands

Run one live Manager request:

```bash
python scripts/run_manager.py \
  --target-page index.html \
  --instruction "Change the button color to dark blue"
```

Run the approved live routing benchmark:

```bash
python scripts/run_routing_benchmark.py
```

Include the optional diagnostic cases:

```bash
python scripts/run_routing_benchmark.py --include-diagnostics
```

The default benchmark report is written to:

```text
reports/routing/manager_routing_benchmark.json
```

This report path is ignored by Git.

### Approved Routing Benchmark Cases

| Case | Expected route |
|---|---|
| Change the button color to dark blue | `execute`: `css` |
| Fix this broken `<div>` tag | `execute`: `html` |
| Make the heading bigger and add missing alt text | `execute`: `html`, `css` |
| Add a contact form with backend validation | `out_of_scope`: no specialists |
| Make it better | `clarification_required`: no specialists |
| Add alt text to the hero image | `execute`: `html` |
| This page will not rank; what is missing? | `execute`: `seo` |
| Add a meta description and make the main heading bigger | `execute`: `seo`, `css` |

Automated tests use fakes and make no live LLM calls. Live Manager and benchmark commands require `.env` values for `GROQ_API_KEY` and `GROQ_MODEL`, require network access, and consume Groq tokens.

HTML/CSS specialist execution is documented below. SEO execution, QA execution, staged-site promotion, Lighthouse production integration, Playwright production integration, Streamlit UI, and full production Flow orchestration remain unimplemented.

## Workspace Tooling Status

The workspace tooling work adds the deterministic staged-workspace and patch tooling layer that future specialists will use:

- `create_staged_copy()` creates one safe server-controlled copy from `sites/working`.
- `WorkspaceHandle` validates one staged run path under `sites/staging`.
- `read_file()` reads bounded line ranges from approved staged `.html` and `.css` files, with strict UTF-8 decoding and a 256 KiB file limit.
- `propose_patch()` detects overlapping exact matches, returns structured applied or rejected results, and enforces specialist file ownership at runtime.
- `ReadFileTool` and `ProposePatchTool` are CrewAI-compatible wrappers bound to one workspace, with the handle and specialist identity hidden from tool arguments.
- Patch writes use same-directory temporary files, flush and sync content, atomically replace the staged target, and restore the original bytes if post-write validation fails.
- `validate_staged_site()` rejects unapproved files, directories, symlinks, structure drift, and any added, removed, renamed, or modified asset.
- Invalid or partially corrupted staged runs can still be safely cleaned without touching `sites/working`, `sites/fixture`, the staging root, or another run.
- `generate_diff()` produces deterministic per-file and combined unified diffs with added/removed line totals and a bounded output size.
- `scripts/demo_workspace.py` demonstrates staging, reading, patching, diff generation, and cleanup without any Groq call.

The workspace tooling remains deterministic infrastructure. HTML/CSS agents consume it only through tools bound by trusted application code; QA acceptance, promotion, browser automation, Lighthouse production execution, and UI work are not implemented here.

## HTML/CSS Specialist Status

This stage adds two production CrewAI specialists and a temporary headless execution preview:

- The HTML specialist owns narrow structural markup, elements, attributes, explicit alt text, labels, semantic markup, and broken heading structure. It may read and patch only the selected target HTML page.
- The CSS specialist owns colors, typography, visual heading size, spacing, borders, layout CSS, and narrow responsive presentation. It may read the selected target page and `style.css`, but may patch only `style.css`.
- Each agent has `allow_delegation=False`, no memory or planning, bounded iterations and retries, and exactly the bound `read_file` and `propose_patch` tools.
- The workspace, specialist identity, and approved assignment files are hidden trusted fields and cannot be supplied or overridden by the model.
- One `PatchEvidenceRecorder` is created per specialist invocation. It records actual applied and rejected `PatchExecutionResult` values in tool-call order; a model completion summary is never treated as proof of a write.
- `SpecialistCompletion` strictly validates the concise `completed` or `blocked` statement. Runtime `succeeded`, `blocked`, and `failed` statuses are derived locally from completion validity and actual patch evidence.
- `SpecialistExecutionService` validates an HTML/CSS-only Manager plan, runs selected specialists sequentially in plan order, stops on blocked or failed work, and generates one authoritative combined staged diff.

Run one explicitly selected specialist:

```bash
python scripts/run_specialist.py \
  --specialist css \
  --target-page index.html \
  --task "Change the primary button background to dark blue"
```

Run the temporary routed HTML/CSS preview:

```bash
python scripts/run_edit_preview.py \
  --target-page index.html \
  --instruction "Make the heading bigger and improve the hero image alt text"
```

Both commands require valid `GROQ_API_KEY` and `GROQ_MODEL` values plus network access and consume Groq tokens. They create a copy under `sites/staging/<run_id>`, never edit `sites/working` or `sites/fixture`, and clean staging in a `finally` block by default. Use `--keep-staging` only when manual inspection is needed.

These commands do not run QA, promote staging, or modify working. SEO execution is rejected before staging. The routed preview is not the final CrewAI Flow. Lighthouse production execution, screenshots, Streamlit, and the final promotion workflow remain later work.

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
GROQ_MODEL=openai/gpt-oss-20b
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

## Workspace Demo

Run the deterministic no-LLM staged-workspace demo:

```bash
python scripts/demo_workspace.py
```

The demo creates a fixed staged run, reads a bounded CSS slice, applies one atomic CSS patch in staging, prints patch and diff evidence, verifies `sites/working` and `sites/fixture` are unchanged, and cleans up the staged run.

Expected generated outputs:

- `reports/routing/manager_trials.json`
- `reports/routing/manager_routing_benchmark.json`
- `reports/lighthouse/seo.json`
- `reports/screenshots/index.png`

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
pytest tests/test_manager_prompt.py -q
pytest tests/test_manager_agent.py -q
pytest tests/test_manager_router.py -q
pytest tests/test_token_usage_normalization.py -q
pytest tests/test_routing_cases.py -q
pytest tests/test_routing_runner.py -q
pytest tests/test_manager_cli.py -q
pytest tests/test_workspace_models.py -q
pytest tests/test_workspace_service.py -q
pytest tests/test_workspace_tools.py -q
pytest tests/test_workspace_demo.py -q
pytest tests/test_specialist_models.py -q
pytest tests/test_specialist_tools_evidence.py -q
pytest tests/test_specialist_prompts.py -q
pytest tests/test_html_agent.py tests/test_css_agent.py -q
pytest tests/test_specialist_runner.py -q
pytest tests/test_specialist_execution.py -q
pytest tests/test_specialist_cli.py tests/test_edit_preview_cli.py -q
```

## Sample Site

The initial static site lives in `sites/fixture`. `sites/working` starts as an identical copy and will become the accepted editable version in later work. `sites/staging` is intentionally empty except for `.gitkeep`.

The site has no JavaScript, remote fonts, CDNs, remote images, or backend behavior.
