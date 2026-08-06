# Final product demo

This 5–10 minute workflow demonstrates the actual product without separate presentation material.

## Prerequisites

- `uv sync --frozen`, `npm ci`, and `uv run playwright install chromium` completed.
- All five role key/model pairs configured in `.env`.
- `uv run python -m pytest -q` and `uv run ruff check .` already pass.
- `uv run python scripts/run_demo.py --check` reports ready.
- The sample site has been intentionally reset with `uv run python scripts/reset_demo_site.py --reset`.

## Recommended sequence

1. Open `sites/working/index.html` through a local static preview if desired.
2. Launch `uv run python scripts/run_ui.py`.
3. Select `index.html`.
4. Submit: `Add a concise meta description based on existing page content and change .hero-copy h1 font-size from 3rem to 3.4rem. Do not change body copy.`
5. Show Manager routing to SEO and CSS.
6. Show sequential specialist events in the timeline.
7. Compare `Before` and the proposed/accepted screenshot.
8. Show applied/rejected patch evidence and the unified diff.
9. Show Lighthouse SEO-only evidence.
10. Show QA’s result for every exact acceptance criterion.
11. Confirm accepted promotion and inspect `sites/working`.
12. Run `uv run python scripts/reset_demo_site.py --reset` and confirm `git diff --no-index -- sites/fixture sites/working` produces no diff.

The same demo can be run from the terminal with explicit safeguards:

```bash
uv run python scripts/run_demo.py --run --apply --reset-first --reset-after
```

## Optional diagnostic demonstration

Run `Diagnose this page's source SEO without editing it.` through the UI or full Flow. Show source findings, Lighthouse evidence, no QA, no promotion, and unchanged working content.

## Failure behavior

Use deterministic tests rather than spending live Groq tokens to force failures:

```bash
uv run python -m pytest tests/test_seo_flow.py tests/test_promotion_service.py -q
```

These cover Lighthouse failure, QA rejection, rollback, and cleanup behavior with fakes.

## Cleanup and artifact inspection

```bash
uv run python scripts/reset_demo_site.py --reset
git diff --no-index -- sites/fixture sites/working
```

PowerShell artifact inspection:

```powershell
Get-ChildItem sites/staging -Force
Get-ChildItem reports/lighthouse -Force
Get-ChildItem reports/screenshots -Force
Get-ChildItem sites -Force | Where-Object Name -Match 'candidate|backup'
```

Generated reports are retained for inspection and ignored by Git. Never delete critical recovery paths blindly.
