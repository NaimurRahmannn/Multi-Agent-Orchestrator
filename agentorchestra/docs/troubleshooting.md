# Troubleshooting

## Python and uv

- **`uv` not found:** install uv, open a new shell, and confirm `uv --version`.
- **`uv sync --frozen` fails:** do not delete `uv.lock`; check Python is within `>=3.10,<3.14` and retry with package-index access.
- **Import failure:** run commands from the application root with `uv run`. Root launchers add `src` for direct script use.
- **Wrong environment:** compare `uv run python --version` and `uv run python -c "import agentorchestra; print(agentorchestra.__file__)"`.

## Groq

- **Missing key/model:** each role needs both matching variables. `.env` must be in the application root used to launch the process.
- **Invalid API key:** verify the failing role, not only Manager; restart Streamlit after `.env` changes because settings are cached.
- **Model unavailable:** update only that role’s model to one enabled for its Groq organization.
- **Rate limit:** wait for the provider retry window, reduce request frequency, use separately authorized organizations, or raise the organization tier. Multiple keys in one organization may share limits.
- **One role works and another fails:** role credentials are intentionally isolated; validate the exact Manager/HTML/CSS/SEO/QA pair.
- **Token metadata unavailable:** the request may still have run; unavailable provider metadata is reported as unknown, never zero.

Never paste keys into logs or issue text.

## CrewAI

- **Deprecation warnings:** warnings from installed CrewAI fields may appear while supported behavior still works; use locked versions and do not treat a warning as execution evidence.
- **Structured-output validation failure:** the model response did not satisfy the local Pydantic contract. Retry a narrow instruction and inspect the safe error.
- **Tool-call failure:** inspect rejected patch evidence. Exact `old_text` must match one current staged substring.
- **Flow import/kickoff problem:** run `uv sync --frozen`, then `uv run python scripts/run_edit_flow.py --help` and the import checks in [Setup](setup.md).

## Lighthouse

- **`npx --no-install lighthouse` unavailable:** run `npm ci` in the application root and check `npx --no-install lighthouse --version`.
- **Missing `node_modules`:** restore it with `npm ci`; do not commit it.
- **Missing Chrome:** install Chrome/Chromium. Playwright’s browser primarily serves screenshots and may not be the executable Lighthouse discovers.
- **Timeout:** close orphaned browser processes, confirm the page is locally servable, and retry.
- **Malformed/missing report:** the audit fails closed and QA does not run for an SEO edit.
- **Windows report cleanup error:** Chrome may temporarily lock its profile after writing a report. AgentOrchestra accepts only a complete schema-valid report; missing/malformed evidence still fails.
- **Local preview failure:** ensure the target exists in `sites/working` or the current staged copy and that loopback traffic is allowed.

## Playwright screenshots

- **Chromium not installed:** run `uv run playwright install chromium`.
- **Executable missing:** run `uv run playwright install --dry-run chromium`, then reinstall the browser.
- **Windows file lock:** stop Streamlit/browser processes and retry; do not delete protected site trees.
- **Screenshot timeout/failure:** the edit may continue with a warning. Review the deterministic diff and QA evidence instead.
- **Screenshot cleanup warning:** retain the report for diagnosis or remove ignored artifacts only after processes release them.

## Streamlit

- **Package unavailable:** run `uv sync --frozen`.
- **Port already in use:** stop the previous Streamlit process before relaunching.
- **Application reruns:** Streamlit reruns on widget interaction; the latest report is kept only in session state.
- **Confirmation required:** select the execution or reset checkbox before its button.
- **Run appears blocked:** review Manager clarification, specialist remaining issue, rejected patches, and the timeline.
- **Stale report/configuration:** stop with `Ctrl+C` and restart after `.env` or code changes. Reset clears the displayed report after success.

## Workspace and patches

- **Staging already exists:** use a new run ID or allow normal cleanup; never point cleanup at the staging root.
- **Unsafe path/symlink rejection:** use a validated top-level sample page and regular files inside the managed site.
- **Asset mismatch:** agents cannot add, remove, rename, or modify assets.
- **`target_not_found`:** reread the exact current source and use its real value (for example, the fixture title is `Harbor Light Studio`, not navigation text `Home`).
- **`ambiguous_target`:** use a longer unique exact block copied from the latest bounded read.
- **Cleanup warning:** accepted content may remain committed; inspect the specifically named managed path after all processes stop.

## Promotion and reset

- **Committed with warning:** working was updated and verified, but one temporary managed path remains.
- **Failed but restored:** the operation failed and the original working digest was restored and verified.
- **Critical recovery required:** stop. Preserve every reported candidate/backup path and inspect it before any manual action.
- **Candidate/backup preserved:** do not delete or rename recovery material blindly; first identify which tree matches the expected digest.

Use `uv run python scripts/reset_demo_site.py --reset` for normal restoration. Do not manually copy fixture over working during recovery because that bypasses transactional validation.
