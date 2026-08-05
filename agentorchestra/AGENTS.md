# AGENTS.md — AgentOrchestra Development Instructions

## 1. Project purpose

AgentOrchestra is a one-week evaluation project that demonstrates reliable multi-agent routing and orchestration for editing a small fixed static HTML/CSS website through natural-language requests.

The primary evaluation question is:

> Can the Manager Agent understand a webpage-editing request and correctly delegate it to the appropriate specialist agent or agents?

The project is not intended to be a production-grade universal webpage editor. Prioritize routing accuracy, visible orchestration, safe staged editing, deterministic evidence, testability, and a reliable supervisor demonstration.

---

## 2. Source of truth

Use the approved **AgentOrchestra Final Updated Project Plan** as the product and architecture baseline.

When requirements conflict, follow this priority:

1. The current user request for the active phase.
2. This `AGENTS.md`.
3. The approved project plan.
4. Existing repository behavior and tests.
5. Framework defaults.

Do not silently invent features outside the approved scope.

---

## 3. Core architecture

The required execution path is:

```text
User request
    ↓
Manager Agent returns a structured routing plan
    ↓
CrewAI Flow validates the plan
    ↓
Flow creates a staged copy
    ↓
Flow invokes only selected specialists
    ↓
Specialists propose narrow patches to staging
    ↓
Flow generates a combined diff and deterministic evidence
    ↓
QA Agent evaluates evidence against acceptance criteria
    ↓
Flow promotes or discards staging
    ↓
Streamlit displays the complete result
```

The architecture sentence that must remain true is:

> Manager decides what should run. Flow decides what actually runs.

### Mandatory orchestration invariants

- The CrewAI Flow is the only execution controller.
- The Manager plans but never executes.
- Agents never invoke one another directly.
- Set `allow_delegation=False` for every agent.
- Only specialists listed in the validated Manager plan may run.
- QA runs automatically after every successful editing sequence.
- QA is not listed in `selected_specialists`.
- The Flow, not an agent, promotes or discards staging.
- Unsupported or ambiguous requests must not create staging or invoke specialists.

---

## 4. Required agents

The required release contains exactly these five agents:

| Agent | Responsibility | Direct tools | Writes |
|---|---|---|---|
| Manager | Classify, route, assign work, define acceptance criteria, clarify, reject | None | No |
| HTML | Structural and markup edits | `read_file`, `propose_patch` | Staged HTML only |
| CSS | Presentation and style edits | `read_file`, `propose_patch` | Staged CSS only |
| SEO | Narrow on-page SEO diagnosis and edits | `read_file`, `propose_patch` | Staged HTML only |
| QA | Evaluate diff and evidence against acceptance criteria | None | No |

### Manager constraints

The Manager may:

- classify the request;
- choose `execute`, `clarification_required`, or `out_of_scope`;
- select HTML, CSS, and/or SEO specialists;
- create one assignment per selected specialist;
- provide a concise routing rationale;
- define measurable acceptance criteria;
- ask one useful clarification question;
- provide an explicit rejection reason.

The Manager must not:

- read files;
- modify files;
- invoke specialists;
- run Lighthouse;
- run Playwright;
- accept or reject staged edits;
- include QA in `selected_specialists`.

### HTML ownership

The HTML Agent handles:

- invalid or broken HTML structure;
- adding or changing HTML elements;
- attributes;
- explicitly requested alt text;
- labels and semantic markup;
- narrow structural changes.

It must not edit CSS or JavaScript.

### CSS ownership

The CSS Agent handles:

- colors;
- typography;
- spacing;
- borders;
- layout-related CSS;
- narrow, demonstrable responsive styling.

It must not edit HTML or JavaScript.

### SEO ownership

The SEO Agent handles only SEO-related HTML work:

- title elements;
- meta descriptions;
- SEO-focused heading hierarchy;
- basic Open Graph metadata when requested;
- narrow on-page SEO diagnosis.

It is not a general HTML agent.

### QA constraints

QA receives structured context and returns only `accept` or `reject` with criterion-level evidence.

QA must not:

- edit files;
- invoke agents;
- run shell commands;
- promote or discard staging.

---

## 5. Required scope

The required version supports:

- a small fixed sample website;
- static `.html` files;
- one shared plain `.css` file;
- HTML structure and attribute changes;
- CSS presentation changes;
- narrow SEO diagnosis and edits;
- natural-language routing;
- single- and multi-specialist requests;
- clarification for ambiguous requests;
- rejection of unsupported requests;
- safe staged edits;
- unified diff generation;
- QA acceptance or rejection;
- Lighthouse SEO-only verification;
- one fixed-viewport before/after screenshot;
- routing evidence;
- model token usage and latency;
- reset to the original fixture.

---

## 6. Explicit non-goals

Do not add any of the following unless the user explicitly changes scope:

- JavaScript editing;
- backend code;
- databases;
- React, Vue, Angular, Svelte, or other frontend frameworks;
- Tailwind, SCSS, Less, or CSS-in-JS;
- arbitrary project uploads;
- arbitrary live-website editing;
- production deployment;
- persistent sessions across restarts;
- database-backed history or rollback;
- multi-viewport testing;
- pixel-diff visual regression;
- Lighthouse Performance, Accessibility, Best Practices, or PWA categories;
- full accessibility certification;
- autonomous internet research;
- browser automation beyond preview, audit, and screenshots;
- production-grade security hardening;
- broad project rewrites.

Accessibility and Content agents are stretch features. Do not implement them until the required five-agent system is complete, tested, and demoable.

---

## 7. Agent tools and backend services

### Agent-facing tools

Only HTML, CSS, and SEO specialists receive direct file tools.

#### `read_file`

Requirements:

- read only approved files inside the current staged sample site;
- use bounded line ranges;
- reject path traversal;
- reject absolute paths;
- reject unsupported file extensions;
- avoid returning the entire project unnecessarily.

#### `propose_patch`

Requirements:

- perform exact `old_text` to `new_text` replacement;
- operate only inside the current staged copy;
- require exactly one match;
- reject zero matches;
- reject multiple matches;
- reject unauthorized files;
- reject unsupported extensions;
- reject empty or no-op replacements;
- reject excessively large changes;
- never modify `sites/working` or `sites/fixture` directly.

### Flow-controlled services

The following are deterministic backend services controlled by the Flow, not tools selected by LLM agents:

- `create_staged_copy()`
- `generate_diff()`
- `run_lighthouse_seo()`
- `capture_screenshot()`
- `promote_staged_copy()`
- `discard_staged_copy()`
- `reset_demo_site()`
- `record_usage()`
- preview-server lifecycle helpers

Do not expose general shell execution to any agent.

---

## 8. Staged-edit safety model

The required directory model is:

```text
sites/
├── fixture/       # immutable original sample site
├── working/       # current accepted version
└── staging/
    └── <run_id>/  # temporary copy for one request
```

Mandatory behavior:

1. Copy `working/` into `staging/<run_id>/`.
2. Specialists modify only that staged copy.
3. Generate a combined diff against `working/`.
4. Run relevant deterministic checks.
5. Ask QA for a verdict.
6. On acceptance, atomically replace `working/` with staging.
7. On rejection or failure, delete staging and keep `working/` unchanged.
8. Reset by replacing `working/` with a clean copy of `fixture/`.

Never edit `fixture/` during normal application execution.

---

## 9. Expected project layout

Keep the generated CrewAI Flow project as the package root and evolve it toward:

```text
agentorchestra/
├── app.py
├── pyproject.toml
├── package.json
├── package-lock.json
├── README.md
├── AGENTS.md
├── .env
├── .env.example
├── .gitignore
│
├── src/
│   └── agentorchestra/
│       ├── __init__.py
│       ├── main.py
│       ├── config.py
│       ├── models.py
│       ├── flow.py
│       ├── crew.py
│       │
│       ├── agents/
│       │   ├── __init__.py
│       │   ├── manager.py
│       │   ├── html_agent.py
│       │   ├── css_agent.py
│       │   ├── seo_agent.py
│       │   └── qa_agent.py
│       │
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── read_file.py
│       │   └── propose_patch.py
│       │
│       ├── services/
│       │   ├── __init__.py
│       │   ├── staging.py
│       │   ├── diff.py
│       │   ├── preview_server.py
│       │   ├── lighthouse.py
│       │   ├── screenshot.py
│       │   └── usage.py
│       │
│       └── evaluation/
│           ├── __init__.py
│           ├── routing_cases.py
│           └── routing_runner.py
│
├── scripts/
│   └── feasibility/
│
├── sites/
│   ├── fixture/
│   ├── working/
│   └── staging/
│
├── reports/
│   ├── screenshots/
│   ├── lighthouse/
│   └── routing/
│
└── tests/
```

Do not create empty future-phase modules merely to imitate the final tree. Add files when their phase requires them.

---

## 10. Phase discipline

Development is phase-by-phase.

For every task:

1. Inspect the current repository before editing.
2. Preserve existing user changes.
3. Implement only the requested phase or subphase.
4. Avoid unrelated refactors.
5. Do not pre-implement later agents or services.
6. Add or update focused tests.
7. Run the requested focused tests.
8. Run an appropriate regression suite.
9. Report exact changed files.
10. Report commands executed and their results.
11. Identify unresolved risks honestly.
12. Suggest one commit message.

Do not move to the next phase unless the user explicitly requests it.

### Current implementation sequence

1. Foundation and feasibility.
2. Configuration and structured domain models.
3. Manager Agent and routing benchmark.
4. Sample site, safe file tools, staging, and diff.
5. HTML and CSS specialist agents.
6. QA Agent and complete Flow.
7. SEO Agent and Lighthouse integration.
8. Streamlit UI, screenshots, and usage metrics.
9. Full test and routing-evidence hardening.
10. Documentation and final demonstration.

---

## 11. Coding standards

### Python

- Target Python 3.12.
- Keep compatibility with the project’s declared Python range.
- Use type hints for public functions and nontrivial internal functions.
- Prefer small cohesive modules.
- Use Pydantic models for validated structured data.
- Prefer enums or `Literal` values for fixed status and verdict fields.
- Use `pathlib.Path` instead of string-based path manipulation.
- Use explicit exceptions with actionable messages.
- Avoid broad `except Exception` unless converting at a well-defined boundary.
- Keep I/O, LLM calls, orchestration, and pure validation logic separated.
- Avoid global mutable state.
- Make deterministic services testable without external processes.
- Use dependency injection or monkeypatch-friendly boundaries for LLMs, subprocesses, clocks, IDs, and filesystem roots.
- Keep prompts in named constants or focused configuration files rather than embedding large prompt text throughout business logic.

### Naming

- Use `snake_case` for modules, functions, and variables.
- Use `PascalCase` for classes and Pydantic models.
- Use meaningful domain names such as `ManagerRoutingPlan`, `SpecialistAssignment`, `QAVerdict`, and `PatchProposal`.
- Do not use vague names such as `data`, `thing`, or `handler` where a domain-specific name is available.

### Comments and documentation

- Document architectural constraints and non-obvious safety rules.
- Do not add comments that merely restate the code.
- Public functions should have concise docstrings when behavior, side effects, or failure modes are not obvious.

---

## 12. Testing requirements

Use `pytest`.

Tests must be deterministic and must not consume real Groq tokens unless explicitly marked as manual feasibility checks.

### Required testing principles

- Mock external LLM calls in automated tests.
- Mock or isolate Lighthouse subprocess execution in unit tests.
- Use temporary directories for filesystem tests.
- Never let tests modify the committed fixture.
- Test success and failure paths.
- Test path traversal and unauthorized files.
- Test exact-match patch behavior.
- Test that `working/` remains unchanged before QA acceptance.
- Test that unsupported and clarification requests invoke no specialists.
- Test that only selected specialists are invoked.
- Test that QA rejection discards staging.
- Test that QA acceptance promotes staging.
- Keep true environment feasibility scripts separate from regular automated tests.

### Typical commands

```bash
pytest -q
pytest tests/test_models.py -q
pytest tests/test_manager_routing.py -q
pytest --cov=agentorchestra --cov-report=term-missing
ruff check .
```

Do not claim tests passed unless they were actually run.

---

## 13. External dependency boundaries

### Groq

- Read independent Manager, HTML, CSS, and QA keys from `GROQ_MANAGER_API_KEY`, `GROQ_HTML_API_KEY`, `GROQ_CSS_API_KEY`, and `GROQ_QA_API_KEY`.
- Read independent model IDs from `GROQ_MANAGER_MODEL`, `GROQ_HTML_MODEL`, `GROQ_CSS_MODEL`, and `GROQ_QA_MODEL`.
- Bind each key/model pair only to its matching agent; do not fall back to another agent's configuration.
- Never hard-code or log secrets.
- Keep the model ID configurable.
- Treat model availability as an environment concern.
- Automated tests must not require a live key.

### Lighthouse

- Invoke the local project binary through `npx lighthouse`.
- Required audits must use only the SEO category.
- Parse JSON into a normalized internal result.
- Time out safely.
- Capture stderr for diagnostics.
- A Lighthouse failure must not crash the Streamlit UI.

### Playwright

- Use Chromium.
- Use one fixed viewport for the required release.
- Ensure browsers and pages close in `finally` blocks or context managers.
- Screenshot paths must be inside the reports directory.
- Preview servers must shut down cleanly.

### Streamlit

- UI code must call application services rather than contain orchestration logic.
- Preserve a clear separation between presentation and Flow execution.
- Display errors without exposing secrets or raw internal stack traces to the supervisor.

---

## 14. Secrets and generated files

Never commit:

- `.env`;
- API keys;
- `.venv/`;
- `node_modules/`;
- temporary staging directories;
- generated Lighthouse reports;
- generated screenshots;
- coverage artifacts;
- cache directories.

Commit:

- `.env.example`;
- `pyproject.toml`;
- `package.json`;
- `package-lock.json`;
- source code;
- tests;
- sample fixture files;
- README and architecture documentation;
- `.gitkeep` files where needed.

---

## 15. Repository safety

Before modifying files:

- inspect `git status`;
- inspect relevant source and tests;
- do not overwrite unrelated changes;
- do not delete generated CrewAI files until their purpose is understood;
- do not initialize another nested Git repository;
- do not move the virtual environment into the package directory;
- do not edit or commit files outside the intended project root.

When replacing template code, remove only files proven to be unused by the new architecture.

---

## 16. Completion report format

At the end of every Codex implementation task, report:

```text
Summary
- What was implemented.

Changed files
- path/to/file: purpose

Commands run
- command
  Result: pass/fail and relevant count

Verification
- Requirement: evidence

Known limitations or risks
- Honest remaining issues

Suggested commit
- type(scope): message
```

If a command could not be run, state why and provide the exact command the user should run locally.

---

## 17. Commit style

Use Conventional Commit-style messages where practical:

- `chore:`
- `feat:`
- `fix:`
- `test:`
- `docs:`
- `refactor:`

Examples:

```text
chore: initialize AgentOrchestra feasibility scaffold
feat(manager): add validated routing plan generation
feat(tools): add bounded file reads and exact staged patches
feat(flow): add staged edit acceptance pipeline
test(routing): add manager routing evidence bank
```

---

## 18. Definition of done for the required release

The project is complete only when:

- the Manager consistently returns valid structured plans;
- the routing evidence bank is measurable and visible;
- only selected specialists execute;
- agents never invoke one another;
- all edits occur in staging;
- ambiguous patch targets are rejected;
- QA evaluates every successful edit sequence;
- rejected edits leave `working/` unchanged;
- accepted edits replace `working/`;
- SEO requests produce a diagnosis or narrow edit;
- Lighthouse runs only the SEO category;
- Streamlit displays routing, execution path, diff, evidence, QA verdict, screenshots, token usage, and latency;
- reset restores the original fixture;
- required automated tests pass;
- setup and demo instructions work in the supervisor environment.
