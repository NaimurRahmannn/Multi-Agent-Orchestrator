from agentorchestra.prompts.specialists import (
    CSS_OWNERSHIP_PROMPT,
    HTML_OWNERSHIP_PROMPT,
    SHARED_SPECIALIST_RULES,
    SPECIALIST_TASK_EXPECTED_OUTPUT,
    build_specialist_task_description,
)
from tests.specialist_helpers import execute_plan, request


def test_html_prompt_enforces_structural_ownership_and_scope():
    prompt = f"{HTML_OWNERSHIP_PROMPT}\n{SHARED_SPECIALIST_RULES}".casefold()

    assert "html structure" in prompt
    assert "alt text" in prompt
    assert "patch only the selected target html page" in prompt
    assert "do not read or patch another html page" in prompt
    assert "do not own colors" in prompt
    assert "page titles or meta descriptions" in prompt
    assert "working files" in prompt
    assert "fixture files" in prompt
    assert "shortest unique exact attribute or element span" in prompt
    assert "never recreate the surrounding markup from memory" in prompt


def test_css_prompt_enforces_visual_ownership_and_scope():
    prompt = f"{CSS_OWNERSHIP_PROMPT}\n{SHARED_SPECIALIST_RULES}".casefold()

    assert "colors" in prompt
    assert "visual heading size" in prompt
    assert "selected target html page and style.css" in prompt
    assert "patch only style.css" in prompt
    assert "do not own html structure" in prompt
    assert "alt text" in prompt
    assert "javascript" in prompt
    assert "broad redesigns" in prompt
    assert "comma-separated selector group" in prompt
    assert "never claim it is missing merely because it is grouped" in prompt
    assert "smallest narrow override" in prompt
    assert "do not change the other grouped selectors" in prompt
    assert "preserve relevant responsive rules" in prompt


def test_shared_prompt_requires_exact_evidence_and_safe_rejection_handling():
    prompt = SHARED_SPECIALIST_RULES.casefold()

    assert "read a bounded range from the same staged file" in prompt
    assert "never guess old_text" in prompt
    assert "copy it exactly" in prompt
    assert "verbatim, contiguous substring" in prompt
    assert "content field" in prompt
    assert "json escaping" in prompt
    assert "preserve every unaffected character" in prompt
    assert "only when propose_patch returns status applied" in prompt
    assert "if every patch attempt was rejected, completed is forbidden" in prompt
    assert "target_not_found" in prompt
    assert "select a different old_text copied verbatim" in prompt
    assert "ambiguous_target" in prompt
    assert "do not bypass" in prompt
    assert "do not delegate" in prompt
    assert "chain-of-thought" in prompt


def test_completion_prompt_forbids_success_without_applied_evidence():
    prompt = SPECIALIST_TASK_EXPECTED_OUTPUT.casefold()

    assert "at least one propose_patch result returned status applied" in prompt
    assert "if all attempts were rejected, return blocked" in prompt


def test_task_context_contains_only_safe_bounded_context():
    plan = execute_plan("html")
    description = build_specialist_task_description(
        specialist=plan.selected_specialists[0],
        request=request(),
        assignment=plan.assignments[0],
        acceptance_criteria=plan.acceptance_criteria,
        allowed_read_files=("index.html",),
        allowed_patch_files=("index.html",),
    )

    assert "index.html" in description
    assert plan.assignments[0].task in description
    assert "task data" in description
    assert "bounded read" in description
    assert "old_text copied from its content field" in description
    assert "completion only after status applied" in description
    assert "staging_root" not in description
    assert "GROQ_API_KEY" not in description
    assert "<!doctype html>" not in description
