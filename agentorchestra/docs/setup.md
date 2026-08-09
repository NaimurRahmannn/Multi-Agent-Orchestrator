# Setup

This guide prepares a clean clone of AgentOrchestra without assuming an existing virtual environment.

## Prerequisites

- Git.
- Python `>=3.11,<3.14` (Python 3.12 is the development target).
- `uv` for locked Python dependency installation.
- Node.js and npm for the locked Lighthouse dependency.
- Chrome/Chromium. Playwright installs its own Chromium for screenshots.
- Groq credentials only for live Manager, specialist, or QA calls.

The final workflow was verified on Windows. Portable commands should work on Linux/macOS, but those platforms were not tested for this release.

## Repository setup

```bash
git clone <repository-url>
cd agentorchestra
uv sync --frozen
npm ci
uv run playwright install chromium
```

`uv sync --frozen` uses `uv.lock`; `npm ci` uses `package-lock.json`. Do not substitute a global Lighthouse installation for the project-local locked package.

## Environment configuration

PowerShell:

```powershell
Copy-Item .env.example .env
```

POSIX shell:

```bash
cp .env.example .env
```

Use placeholders only in shared documentation:

```dotenv
GROQ_MANAGER_API_KEY=your_manager_key
GROQ_MANAGER_MODEL=llama-3.3-70b-versatile
GROQ_HTML_API_KEY=your_html_key
GROQ_HTML_MODEL=llama-3.3-70b-versatile
GROQ_CSS_API_KEY=your_css_key
GROQ_CSS_MODEL=openai/gpt-oss-20b
GROQ_SEO_API_KEY=your_seo_key
GROQ_SEO_MODEL=llama-3.3-70b-versatile
GROQ_QA_API_KEY=your_qa_key
GROQ_QA_MODEL=llama-3.3-70b-versatile
APP_ENV=development
LOG_LEVEL=INFO
```

`AGENTORCHESTRA_ROOT` is optional and normally unset. Set it only to an absolute application root containing `src`, `sites`, and `reports`. Each live role requires its matching key and model; a working Manager key does not replace a missing QA key. Keys may belong to separate authorized Groq organizations.

## Runtime checks

```bash
uv run python --version
uv run python -c "import crewai, streamlit, playwright; print(crewai.__version__); print(streamlit.__version__); print('Playwright import available')"
node --version
npm --version
npx --no-install lighthouse --version
uv run playwright install --dry-run chromium
uv run python scripts/run_demo.py --check
```

The demo check reports only booleans; it makes no Groq call, audit, screenshot, or site mutation.

## Initial verification

```bash
uv run python -c "import agentorchestra; from agentorchestra.flow import AgentOrchestraFlow; print('imports successful')"
uv run python -m pytest -q
uv run ruff check .
git diff --check
uv run python scripts/verify_clean_install.py --check
uv run python scripts/run_edit_flow.py --help
uv run python scripts/run_ui.py --help
```

Reset only when you intentionally want `sites/working` to match the committed fixture:

```bash
uv run python scripts/reset_demo_site.py --reset
```

## Windows notes

- Activate a uv-created environment manually with `.venv\Scripts\Activate.ps1` only if desired; `uv run` does not require activation.
- Quote instructions with double quotes in PowerShell. Embedded double quotes require PowerShell escaping; phrasing exact changes without literal quotes is simpler.
- Some symlink tests skip when Windows cannot create symlinks.
- Chrome can leave its temporary Lighthouse profile locked briefly. AgentOrchestra accepts only a complete, schema-valid report and fails closed if the report is missing or malformed.
- If browser files are locked, close Streamlit/Chrome processes before retrying cleanup or reset.

## Linux and macOS notes

Use `/` paths and `source .venv/bin/activate` if activating manually. Browser executable discovery and sandbox packages vary by distribution; install platform prerequisites reported by Playwright or Chrome. No platform guarantee beyond the tested Windows environment is claimed.

## Security notes

- `.env` is ignored and must never be committed.
- Agent tools receive hidden workspace and specialist bindings; models cannot choose arbitrary paths.
- UI and CLIs accept only validated sample-site page names, not URLs or project uploads.
- Preview and screenshot traffic is restricted to loopback.
- Groq keys are bound per role and redacted from user-facing errors.
- Raw provider responses are not persisted in final Flow reports.
