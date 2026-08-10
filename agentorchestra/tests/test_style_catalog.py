from agentorchestra.services.style_catalog import (
    components_for_page,
    deterministic_style_plan,
    load_style_components,
)
from agentorchestra.style_models import StyleOperation, StylePlanStatus


def test_component_catalog_has_unique_stable_ids():
    components = load_style_components()

    assert components
    assert len({component.id for component in components}) == len(components)
    assert {component.id for component in components_for_page("index.html")} >= {
        "index.page",
        "index.hero",
        "index.hero.project_cta",
        "shared.header",
    }


def test_common_nontechnical_prompts_get_deterministic_semantic_plans():
    cases = [
        (
            "Make the page background light gray.",
            "index.page",
            StyleOperation.SET_BACKGROUND_COLOR,
            "#e5e7eb",
        ),
        (
            "Change the Start a project button to red.",
            "index.hero.project_cta",
            StyleOperation.SET_BACKGROUND_COLOR,
            "#dc2626",
        ),
        (
            "Change the green Start a project button to red.",
            "index.hero.project_cta",
            StyleOperation.SET_BACKGROUND_COLOR,
            "#dc2626",
        ),
        (
            "Make the home page hero section shorter.",
            "index.hero",
            StyleOperation.DECREASE_HEIGHT,
            None,
        ),
        (
            "Give the Start a project button more rounded corners.",
            "index.hero.project_cta",
            StyleOperation.INCREASE_BORDER_RADIUS,
            None,
        ),
    ]

    for instruction, target_id, operation, value in cases:
        plan = deterministic_style_plan(target_page="index.html", instruction=instruction)
        assert plan is not None
        assert plan.status is StylePlanStatus.EXECUTE
        assert plan.target_id == target_id
        assert plan.operation is operation
        assert plan.value == value


def test_unknown_intent_falls_back_to_semantic_planner():
    assert (
        deterministic_style_plan(
            target_page="index.html",
            instruction="Make the design feel more adventurous.",
        )
        is None
    )
