# AgentOrchestra

AgentOrchestra is an incremental CrewAI evaluation project for routing natural-language requests to specialist agents that edit a small static HTML/CSS website.

The central architecture rule is: **Manager decides what should run. Flow decides what actually runs.**

## Implementation Status

The project includes its original feasibility checks plus the production lifecycle:

- Typed environment configuration for Groq-backed operations.
- Strict Pydantic routing models for Manager output.
- A fixed local static sample site in `sites/fixture` with an identical starting copy in `sites/working`.
- Manual feasibility checks for CrewAI imports, Groq connectivity, structured Manager output, Lighthouse SEO-only execution, and Playwright Chromium screenshots.
- Deterministic tests that do not call Groq, Lighthouse, or browser automation.

Production Manager routing, staged workspace tooling, HTML/CSS/SEO specialist execution, SEO diagnostics, SEO-only Lighthouse evidence, before/proposed-after screenshots, structured timeline and metrics, QA-controlled promotion, transactional reset, and the Streamlit supervisor UI are implemented. Accessibility/content agents, JavaScript editing, arbitrary sites, visual-regression scoring, and non-SEO Lighthouse categories are not implemented.

## Domain Contracts Status

The domain-contract work adds the production configuration and strict models future work will use:

- `EditRequest` validates safe single-page static HTML edit requests.
- `ManagerRoutingPlan` validates `execute`, `clarification_required`, and `out_of_scope` routing plans.
- `PatchProposal` validates specialist patch proposals but does not edit files.
- `RoutingEvidenceCase`, `RoutingEvidenceResult`, and `TokenUsage` define routing benchmark evidence.
- `QAResult` and `CriterionResult` validate QA verdict structure but do not execute QA.
- `validate_qa_coverage()` checks QA criterion coverage against Manager acceptance criteria.

Supported routing statuses are `execute`, `clarification_required`, and `out_of_scope`. Supported specialist names are `html`, `css`, and `seo`; QA is intentionally not a selectable specialist.

Production QA, Flow orchestration, SEO contracts, normalized Lighthouse evidence, screenshot observability, and the Streamlit UI are documented below.

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

Automated tests use fakes and make no live LLM calls. Live Manager and benchmark commands require `.env` values for `GROQ_MANAGER_API_KEY` and `GROQ_MANAGER_MODEL`, require network access, and consume Groq tokens.

HTML/CSS/SEO specialist execution and QA-controlled promotion are documented below.

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

The workspace tooling remains deterministic infrastructure. Specialists consume it only through tools bound by trusted application code; QA acceptance and promotion are controlled by the Flow.

## HTML/CSS/SEO Specialist Status

The production specialists are:

- The HTML specialist owns narrow structural markup, elements, attributes, explicit alt text, labels, semantic markup, and broken heading structure. It may read and patch only the selected target HTML page.
- The CSS specialist owns colors, typography, visual heading size, spacing, borders, layout CSS, and narrow responsive presentation. It may read the selected target page and `style.css`, but may patch only `style.css`.
- The SEO specialist owns page titles, meta descriptions, requested basic Open Graph metadata, SEO-focused heading hierarchy, and source-based SEO diagnosis. It may access only the selected HTML page.
- Each agent has `allow_delegation=False`, no memory or planning, bounded iterations and retries, and exactly the bound `read_file` and `propose_patch` tools.
- The workspace, specialist identity, and approved assignment files are hidden trusted fields and cannot be supplied or overridden by the model.
- One `PatchEvidenceRecorder` is created per specialist invocation. It records actual applied and rejected `PatchExecutionResult` values in tool-call order; a model completion summary is never treated as proof of a write.
- `SpecialistCompletion` strictly validates the concise `completed` or `blocked` statement. Runtime `succeeded`, `blocked`, and `failed` statuses are derived locally from completion validity and actual patch evidence.
- `SpecialistExecutionService` validates HTML/CSS/SEO Manager plans, runs selected specialists sequentially in plan order, stops on blocked or failed work, and generates one authoritative combined staged diff.

Run one explicitly selected specialist:

```bash
python scripts/run_specialist.py \
  --specialist css \
  --target-page index.html \
  --task "Change the primary button background to dark blue"
```

Run SEO edit or read-only diagnostic mode explicitly:

```bash
python scripts/run_specialist.py --specialist seo --mode edit --target-page index.html --task "Improve the page title"
python scripts/run_specialist.py --specialist seo --mode diagnostic --target-page index.html --task "Diagnose source SEO"
```

HTML and CSS reject `--mode diagnostic`. Single-specialist diagnostic mode returns source findings but does not run Lighthouse; use the full Flow for the combined diagnostic report.

Run the temporary routed HTML/CSS preview:

```bash
python scripts/run_edit_preview.py \
  --target-page index.html \
  --instruction "Make the heading bigger and improve the hero image alt text"
```

The routed command uses the Manager's key/model pair for routing and each selected specialist's matching key/model pair for editing. The single-specialist command requires only its matching specialist pair. Both commands require network access and consume Groq tokens. They create a copy under `sites/staging/<run_id>`, never edit `sites/working` or `sites/fixture`, and clean staging in a `finally` block by default. Use `--keep-staging` only when manual inspection is needed.

Live Manager and specialist LLM calls use a small provider retry budget for transient Groq rate limits. Short `retry after` TPM windows may pause and retry the same provider request up to two times. CrewAI agent and task retries remain disabled, so failed patch workflows are not replayed as a separate agent run.

These preview commands do not run QA, Lighthouse, promote staging, or modify working. Use the QA-controlled Flow below for accepted updates or a complete SEO diagnostic.

## SEO and Lighthouse Status

SEO edit mode has the same exact-patch evidence requirement as HTML/CSS edits. SEO diagnostic mode receives only `read_file`, must return non-empty structured source findings, must produce no patch evidence, and must leave the staged diff empty.

After selected specialists succeed, the Flow runs Lighthouse only when SEO was selected. The service validates the staged site, serves it on `127.0.0.1` with an ephemeral port, invokes the project-local Lighthouse package with `shell=False`, headless Chrome, a timeout, and only `--only-categories=seo`, then always stops the server. Raw JSON is stored under `reports/lighthouse/`; pipeline reports contain only normalized SEO score, audit items, failed audit IDs, safe errors, and latency.

Audit the protected working copy directly without Groq or mutation:

```bash
python scripts/run_lighthouse_seo.py --target-page index.html --apply
```

`--apply` is required before this command launches Lighthouse. It never edits `sites/working` or `sites/fixture`.

## QA-Controlled Edit Flow Status

The production edit lifecycle is an explicit CrewAI Flow transition graph. `kickoff()` is the
authoritative entry point; `run()` remains only as a compatibility wrapper that delegates to it:

```text
EditRequest
  -> @start Manager routing
  -> Manager router: clarify / out of scope / executable
  -> fresh staging transition
  -> before screenshot from protected working content
  -> selected HTML/CSS/SEO specialists in Manager order
  -> specialist-result router
  -> SEO-only Lighthouse when SEO is selected
  -> SEO diagnostic returns findings + audit without QA or promotion
  -> deterministic evidence and content-digest validation
  -> proposed-after screenshot from the exact reviewed staged digest
  -> tool-free QA Agent
  -> QA verdict router
  -> QA accept promotes; every other staged outcome discards
```

QA receives a deterministic evidence bundle containing the original request, Manager-selected specialists and assignments, exact acceptance criteria, actual applied/rejected patch metadata, changed files, the final staged diff, and matching normalized Lighthouse evidence for SEO edits. Raw Lighthouse JSON is unavailable to QA. SEO scores cannot prove unrelated HTML/CSS criteria or search-ranking improvement. QA has no tools and cannot edit files, invoke agents, promote staging, or discard staging. Each Manager acceptance criterion must be returned exactly once; QA accepts only when every criterion passes and rejects insufficient evidence.

Before QA runs, the Flow validates that the specialist report succeeded, selected specialists match execution order, at least one patch was applied, changed files exactly match applied patch files, ownership boundaries are respected, the diff is non-empty, and no asset or absolute path appears in structured user-facing evidence. Normal HTML closing tags, CSS URLs, and web URLs in unified diff content are not treated as filesystem paths.

The Flow records a deterministic SHA-256 digest over every validated site-relative path and its exact bytes. On acceptance it revalidates both the reviewed diff and QA evidence digest, then requires the staged, promotion-candidate, and installed working trees to have the same content digest. Digest equality complements rather than replaces exact diff equality.

Run the full Flow only when you intend to allow live Groq calls and working-site promotion:

```bash
python scripts/run_edit_flow.py \
  --target-page index.html \
  --instruction "Change the primary call-to-action button background to dark blue" \
  --apply
```

SEO edit and diagnostic examples:

```bash
python scripts/run_edit_flow.py --target-page index.html --instruction "Improve the page title and meta description" --apply
python scripts/run_edit_flow.py --target-page index.html --instruction "Diagnose this page's source SEO without editing it" --apply
```

For `request_type=seo_diagnostic`, the Manager must select only SEO. The Flow confirms an empty diff, runs Lighthouse SEO, returns `diagnostic_completed`, skips QA and promotion, cleans staging, and leaves working and fixture unchanged.

Without `--apply`, the command exits before any Groq call or mutation:

```bash
python scripts/run_edit_flow.py \
  --target-page index.html \
  --instruction "Change the primary call-to-action button background to dark blue"
```

Outcome exit codes:

| Outcome | Exit code |
|---|---:|
| accepted | 0 |
| diagnostic_completed | 0 |
| failed | 1 |
| missing `--apply` / usage guard | 2 |
| rejected | 4 |
| clarification_required | 5 |
| out_of_scope | 6 |
| unsupported_specialist | 7 |
| blocked | 8 |
| critical working-site recovery required | 9 |

On QA accept, promotion copies staging to a uniquely generated candidate, proves its digest matches staging, renames working to a backup, installs the candidate, and proves the final working digest matches the accepted content. A commit/validation failure restores and verifies the original working digest. If restoration cannot be completed, the core Flow propagates a critical recovery error and preserves recovery material; the CLI returns exit code 9.

After a proven commit, failure to remove an obsolete backup, candidate, or staged run is reported as `committed_with_warning`. The edit remains accepted and `working updated` remains true, while each cleanup flag and warning stays honest. Clean commits remove all transaction paths. No persistent version history is kept.

Reset the demo working site from the fixture with explicit confirmation:

```bash
python scripts/reset_demo_site.py --reset
```

The reset command uses the same candidate, digest, commit, verified rollback, and post-commit warning semantics. It does not remove unrelated staging runs. Reset cleanup warnings remain successful resets; a rollback failure returns the same critical exit code 9.

Screenshot failures are observability warnings and do not normally block specialists, QA, or promotion. Unsafe screenshot path boundaries still fail closed. Screenshots never enter the QA evidence digest. Accessibility/content agents, JavaScript editing, arbitrary sites, visual pixel-diff logic, and Performance/Accessibility/Best Practices/PWA Lighthouse categories remain excluded.

## Streamlit Supervisor UI

Launch the production dashboard from the application root:

```bash
uv run streamlit run src/agentorchestra/ui/app.py
```

The fixed-path wrapper is equivalent:

```bash
python scripts/run_ui.py
```

The UI exposes only validated top-level working-site HTML pages. A confirmation checkbox is required before a run can create staging or call Groq, Lighthouse, Playwright, QA, or promotion. A separate confirmation protects the existing transactional reset. It renders routing, actual timeline events, before/proposed-after evidence, specialists, patch evidence, deterministic diff, normalized Lighthouse SEO evidence, QA results, aggregate metrics, warnings, and in-memory sanitized downloads.

Accepted runs label the staged screenshot `After — applied`. Rejected, blocked, and failed runs label it `Proposed result — not applied`; working remains unchanged. SEO diagnostics launch no screenshots, skip QA and promotion, and report why.

Screenshot files are generated under `reports/screenshots/<run-id>/` and ignored by Git. They are fixed 1440×900 desktop, full-page captures served from an ephemeral loopback preview server. Browser requests to external HTTP/HTTPS origins are aborted. Reports contain only project-relative screenshot paths, and the UI revalidates the path and symlink boundary before reading image bytes.

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
GROQ_MANAGER_API_KEY=
GROQ_HTML_API_KEY=
GROQ_CSS_API_KEY=
GROQ_SEO_API_KEY=
GROQ_MANAGER_MODEL=llama-3.3-70b-versatile
GROQ_HTML_MODEL=llama-3.3-70b-versatile
GROQ_CSS_MODEL=openai/gpt-oss-20b
GROQ_SEO_MODEL=llama-3.3-70b-versatile
GROQ_QA_API_KEY=
GROQ_QA_MODEL=llama-3.3-70b-versatile
APP_ENV=development
LOG_LEVEL=INFO
AGENTORCHESTRA_ROOT=
```

Configuration is loaded with `pydantic-settings`. `AGENTORCHESTRA_ROOT` is optional and is mainly useful for tests or unusual local layouts; derived site and report paths remain under that root. Runtime-directory creation is explicit and limited to staging and report directories.

Each Groq key/model pair is bound to only its named agent: Manager, HTML, CSS, SEO, and QA. There is no silent credential fallback. To distribute provider quotas, use keys from the separate Groq organizations authorized for those agents; keys from one organization still share that organization's limits. Model IDs may differ by agent, but each must be available to its corresponding organization. Never commit the populated `.env` file.

Install Python dependencies with the project toolchain:

```bash
crewai install
```

Install Node dependencies for Lighthouse from this app root:

```bash
npm install
```

Install Playwright Chromium in the same virtual environment used to run AgentOrchestra:

```bash
python -m playwright install chromium
```

For a local-only screenshot smoke test that makes no Groq or Lighthouse call:

```bash
python scripts/capture_page_screenshot.py --target-page index.html
```

If the screenshot reports Playwright or Chromium unavailable, first confirm the virtual environment is active, then rerun the Chromium installation command above. If Lighthouse is unavailable, run `npm install` from this application root. Groq rate-limit failures remain provider/organization quota failures; screenshots and shorter instructions do not remove an organization-level TPM limit.

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
- `reports/lighthouse/seo-<run-id>-<report-id>.json`
- `reports/screenshots/<run-id>/before-<page>.png`
- `reports/screenshots/<run-id>/proposed-after-<page>.png`

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
pytest tests/test_seo_agent.py tests/test_seo_models.py -q
pytest tests/test_specialist_runner.py -q
pytest tests/test_specialist_execution.py -q
pytest tests/test_specialist_cli.py tests/test_edit_preview_cli.py -q
pytest tests/test_pipeline_models.py tests/test_qa_prompt.py tests/test_qa_agent.py -q
pytest tests/test_qa_output.py tests/test_qa_runner.py tests/test_qa_evidence.py -q
pytest tests/test_promotion_service.py tests/test_edit_flow.py -q
pytest tests/test_edit_flow_cli.py tests/test_reset_demo_site.py -q
pytest tests/test_flow_transitions.py tests/test_site_digest.py tests/test_path_safety.py -q
pytest tests/test_preview_server_service.py tests/test_lighthouse.py tests/test_lighthouse_cli.py -q
pytest tests/test_seo_flow.py -q
```

## Sample Site

The initial static site lives in `sites/fixture`. `sites/working` starts as an identical copy and will become the accepted editable version in later work. `sites/staging` is intentionally empty except for `.gitkeep`.

The site has no JavaScript, remote fonts, CDNs, remote images, or backend behavior.
