import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "feasibility" / "check_manager_output.py"
SPEC = importlib.util.spec_from_file_location("check_manager_output", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_crewai_model_name_preserves_groq_prefixed_model():
    assert MODULE._crewai_model_name("groq/llama-3.3-70b-versatile") == "groq/llama-3.3-70b-versatile"


def test_crewai_model_name_adds_groq_prefix_to_plain_model():
    assert MODULE._crewai_model_name("llama-3.3-70b-versatile") == "groq/llama-3.3-70b-versatile"


def test_crewai_model_name_adds_groq_prefix_to_namespaced_groq_model():
    assert MODULE._crewai_model_name("openai/gpt-oss-20b") == "groq/openai/gpt-oss-20b"


def test_crewai_model_name_rejects_blank_model():
    try:
        MODULE._crewai_model_name("  ")
    except ValueError as exc:
        assert "GROQ_MODEL" in str(exc)
    else:
        raise AssertionError("blank model should fail")


def test_extract_json_object_accepts_markdown_wrapped_json():
    raw = '```json\n{"status": "out_of_scope"}\n```'

    assert MODULE._extract_json_object(raw) == '{"status": "out_of_scope"}'


def test_extract_json_object_accepts_prefixed_text():
    raw = 'Here is the plan:\n{"status": "out_of_scope"}\nDone.'

    assert MODULE._extract_json_object(raw) == '{"status": "out_of_scope"}'


def test_disable_crewai_prompt_cache_breakpoints():
    MODULE._disable_crewai_prompt_cache_breakpoints()

    from crewai.llms import cache as crewai_cache

    message = {"role": "system", "content": "hello"}

    assert crewai_cache.mark_cache_breakpoint(message) == message


def test_generate_plan_retries_after_invalid_json(monkeypatch):
    class FakeLlm:
        def __init__(self):
            self.calls = 0

        def call(self, messages, from_agent):
            self.calls += 1
            if self.calls == 1:
                return "not json"
            return (
                '{"status":"execute","request_type":"css_change",'
                '"selected_specialists":["css"],'
                '"routing_rationale":"CSS-only visual change.",'
                '"assignments":[{"agent":"css","task":"Update button color."}],'
                '"acceptance_criteria":["Button color is updated."],'
                '"clarification_question":null,"rejection_reason":null}'
            )

    monkeypatch.setenv("MANAGER_LLM_MAX_ATTEMPTS", "2")

    raw, plan, attempts = MODULE._generate_plan_with_retries(
        FakeLlm(),
        MODULE.CASES[0],
        manager=object(),
    )

    assert raw.startswith('{"status"')
    assert plan.status == "execute"
    assert attempts == 2
