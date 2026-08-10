from agentorchestra.agents.css_agent import build_css_agent, build_css_task
from agentorchestra.config import GroqConfiguration
from agentorchestra.style_models import StyleIntentPlan
from tests.specialist_helpers import execute_plan, request


def test_css_agent_is_a_single_turn_tool_free_semantic_planner(tmp_path):
    agent = build_css_agent(
        workspace=None,
        target_page="index.html",
        groq=GroqConfiguration(api_key="secret", model="test-model"),
    )

    assert agent.allow_delegation is False
    assert agent.verbose is False
    assert not agent.memory
    assert agent.planning is False
    assert agent.reasoning is False
    assert agent.max_iter == 1
    assert agent.max_retry_limit == 0
    assert agent.tools == []
    assert agent.llm.max_tokens == 500
    assert agent.llm.timeout == 120


def test_css_task_supplies_only_semantic_catalog_data(tmp_path):
    agent = build_css_agent(
        workspace=None,
        target_page="index.html",
        groq=GroqConfiguration(api_key="secret", model="test-model"),
    )
    plan = execute_plan("css")
    task = build_css_task(
        agent=agent,
        request=request(),
        assignment=plan.assignments[0],
        acceptance_criteria=plan.acceptance_criteria,
    )

    assert "index.html" in task.description
    assert "Trusted component catalog" in task.description
    assert '"target_id": "index.hero.project_cta"' in task.description
    assert "Never edit files or call tools" in task.description
    assert "<!doctype html>" not in task.description
    assert "background: var(--accent)" not in task.description
    assert task.output_pydantic is StyleIntentPlan
