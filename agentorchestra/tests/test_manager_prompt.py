from agentorchestra.prompts.manager import MANAGER_SYSTEM_PROMPT


def test_manager_prompt_contains_ownership_boundaries():
    prompt = MANAGER_SYSTEM_PROMPT.casefold()

    assert "html" in prompt
    assert "broken tags" in prompt
    assert "css" in prompt
    assert "colors" in prompt
    assert "seo" in prompt
    assert "meta description" in prompt
    assert "seo_edit" in prompt
    assert "seo_diagnostic" in prompt
    assert "structured request_type is authoritative" in prompt


def test_manager_prompt_contains_required_routing_rules():
    prompt = MANAGER_SYSTEM_PROMPT.casefold()

    assert "alt text" in prompt
    assert "html" in prompt
    assert "visually enlarge heading" in prompt
    assert "css" in prompt
    assert "page title" in prompt
    assert "clarification_required" in prompt
    assert "javascript" in prompt
    assert "backend" in prompt
    assert "qa is never selectable" in prompt
    assert "do not read files" in prompt
    assert "use no tools" in prompt
    assert "delegation" in prompt
    assert "measurable" in prompt
