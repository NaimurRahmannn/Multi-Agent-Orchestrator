from agentorchestra.services.style_compiler import execute_style_plan
from agentorchestra.services.workspace import create_staged_copy
from agentorchestra.style_models import StyleExecutionStatus, StyleIntentPlan
from tests.test_workspace_service import make_settings


def _workspace_with_catalog_css(tmp_path, *, run_id: str):
    settings = make_settings(tmp_path)
    css = """body {
  background: var(--paper);
}

.hero-section {
  gap: 48px;
  min-height: 520px;
  padding: 56px 0;
}

.button-link {
  border-radius: 6px;
  padding: 12px 18px;
  background: green;
  color: #ffffff;
}
"""
    (settings.working_site_dir / "style.css").write_text(css, encoding="utf-8")
    return create_staged_copy(settings=settings, run_id_factory=lambda: run_id)


def test_compiler_applies_normalized_color_with_semantic_evidence(tmp_path):
    workspace = _workspace_with_catalog_css(tmp_path, run_id="style-color")
    plan = StyleIntentPlan(
        status="execute",
        target_id="index.page",
        operation="set_background_color",
        value="#e5e7eb",
        summary="Make the page background light gray.",
    )

    result = execute_style_plan(plan, target_page="index.html", workspace=workspace)

    assert result.status is StyleExecutionStatus.APPLIED
    assert result.patch is not None
    assert result.evidence is not None
    assert result.evidence.before_value == "var(--paper)"
    assert result.evidence.after_value == "#e5e7eb"
    assert result.evidence.source_verified is True
    assert "background: #e5e7eb;" in (workspace.path / "style.css").read_text()


def test_compiler_changes_the_existing_declaration_instead_of_adding_duplicate(tmp_path):
    workspace = _workspace_with_catalog_css(tmp_path, run_id="style-radius")
    plan = StyleIntentPlan(
        status="execute",
        target_id="index.hero.project_cta",
        operation="increase_border_radius",
        amount="slight",
        summary="Round the project button slightly.",
    )

    result = execute_style_plan(plan, target_page="index.html", workspace=workspace)
    content = (workspace.path / "style.css").read_text()

    assert result.status is StyleExecutionStatus.APPLIED
    assert result.evidence is not None
    assert result.evidence.before_value == "6px"
    assert result.evidence.after_value == "8px"
    assert content.count("border-radius:") == 1


def test_compiler_maps_unspecified_shorter_to_a_moderate_height_token(tmp_path):
    workspace = _workspace_with_catalog_css(tmp_path, run_id="style-height")
    plan = StyleIntentPlan(
        status="execute",
        target_id="index.hero",
        operation="decrease_height",
        summary="Make the home page hero section shorter.",
    )

    result = execute_style_plan(plan, target_page="index.html", workspace=workspace)

    assert result.status is StyleExecutionStatus.APPLIED
    assert result.evidence is not None
    assert result.evidence.before_value == "520px"
    assert result.evidence.after_value == "420px"


def test_compiler_returns_already_satisfied_without_a_patch(tmp_path):
    workspace = _workspace_with_catalog_css(tmp_path, run_id="style-noop")
    plan = StyleIntentPlan(
        status="execute",
        target_id="index.hero.project_cta",
        operation="set_background_color",
        value="green",
        summary="Make the project button green.",
    )

    result = execute_style_plan(plan, target_page="index.html", workspace=workspace)

    assert result.status is StyleExecutionStatus.ALREADY_SATISFIED
    assert result.patch is None
    assert result.evidence is not None


def test_compiler_preserves_clarification_as_a_first_class_outcome(tmp_path):
    workspace = _workspace_with_catalog_css(tmp_path, run_id="style-question")
    plan = StyleIntentPlan(
        status="clarification_required",
        summary="The requested button is ambiguous.",
        clarification_question="Which button should change?",
    )

    result = execute_style_plan(plan, target_page="index.html", workspace=workspace)

    assert result.status is StyleExecutionStatus.CLARIFICATION_REQUIRED
    assert result.clarification_question == "Which button should change?"
    assert result.patch is None
