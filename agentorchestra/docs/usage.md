# Usage

## Streamlit workflow

1. Run `uv run python scripts/run_ui.py`.
2. Select a validated target page.
3. Enter a plain-language instruction. For cataloged CSS targets, selectors and current values are not required.
4. Confirm that live services and accepted promotion may run.
5. Select **Run edit**.
6. Review Manager routing and assignments.
7. Review the ordered execution timeline.
8. Compare `Before` with `After — applied` or `Proposed result — not applied`.
9. Inspect patch evidence and the deterministic unified diff.
10. Inspect Lighthouse SEO evidence when SEO was selected.
11. Inspect QA criterion results and the final outcome.
12. Use the separately confirmed reset when you want to restore the fixture.

Screenshots help a human review the run but are excluded from QA evidence.

## Fixture-compatible requests

- HTML: `In index.html, change the hero image alt text from Abstract lighthouse and wave mark to Lighthouse above ocean waves. Do not change anything else.`
- CSS: `Change the Start a project button to red.`
- CSS: `Make the page background light gray.`
- CSS: `Make the home page hero section shorter.`
- CSS: `Give the Start a project button more rounded corners.`
- SEO edit: `In index.html, replace the title Harbor Light Studio with Harbor Light Web Design Studio and add a concise meta description based only on existing page content. Do not change body content.`
- SEO diagnostic: `Diagnose this page's source SEO without editing it.`
- HTML + CSS: `Change the hero image alt text to Lighthouse above ocean waves and change .hero-copy h1 font-size from 3rem to 3.4rem. Do not change anything else.`
- SEO + CSS: `Add a concise meta description based on existing page content and change .hero-copy h1 font-size from 3rem to 3.4rem. Do not change body copy.`

Cataloged CSS requests do not need filenames, selectors, properties, or exact current values. If a target is genuinely ambiguous, the result contains one clarification question. The fixture title is `Harbor Light Studio`, not `Home` (which is navigation text).

## CLI workflows

```bash
# Full Flow; can promote only after QA accepts
uv run python scripts/run_edit_flow.py --target-page index.html --instruction "In style.css, inside .button-link, change background from var(--accent) to #0b3d91. Do not change anything else." --apply

# Staging-only specialist
uv run python scripts/run_specialist.py --specialist html --target-page index.html --task "Change the hero image alt text from Abstract lighthouse and wave mark to Lighthouse above ocean waves."

# Read-only working-site Lighthouse audit
uv run python scripts/run_lighthouse_seo.py --target-page index.html --apply

# Screenshot capture
uv run python scripts/capture_page_screenshot.py --target-page index.html --apply

# Transactional reset and UI
uv run python scripts/reset_demo_site.py --reset
uv run python scripts/run_ui.py
```

`--apply` is an execution confirmation, not an instruction to bypass QA. The specialist preview never promotes working.

## Outcome meanings

| Outcome | Meaning |
|---|---|
| `accepted` | QA accepted every criterion and promotion committed. |
| accepted with warning | Content committed, but a temporary candidate, backup, or staging cleanup needs attention. |
| `rejected` | QA found insufficient evidence; staging was discarded. |
| `diagnostic_completed` | SEO findings and Lighthouse evidence returned with no edit, QA, or promotion. |
| `clarification_required` | Manager or semantic CSS planner needs one specific answer; no change is promoted. |
| `already_satisfied` | The requested cataloged style already exists; QA and promotion are skipped. |
| `out_of_scope` | Request is unsupported; no staging or specialist ran. |
| `blocked` | A selected specialist could not complete its assignment safely. |
| `failed` | Configuration, specialist, audit, evidence, or other controlled execution failed. |
| critical recovery required | Promotion/reset rollback could not be proven; preserve reported recovery paths. |

## Metrics

- Latency is measured per stage and for the total run.
- Token usage is reported per executed agent when provider metadata exists. `unavailable` is unknown, not zero.
- Patch counts distinguish applied evidence from rejected attempts.
- Changed files and diff totals come from the staged/working comparison.
- Lighthouse score and failed audit IDs cover only the SEO category.
- Screenshot timing is observability only and does not affect QA.
