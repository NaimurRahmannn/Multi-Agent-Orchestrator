import importlib.util
import sys
from pathlib import Path

from agentorchestra.models import ManagerRunResult

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
        assert "Groq model" in str(exc)
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


def test_run_case_uses_production_manager_router_once(monkeypatch):
    calls = []

    class FakeRouter:
        def __init__(self, settings):
            self.settings = settings

        def route(self, request):
            calls.append(request)
            return ManagerRunResult(
                request=request,
                plan=MODULE._extract_plan(
                    '{"status":"execute","request_type":"css_change",'
                    '"selected_specialists":["css"],'
                    '"routing_rationale":"CSS-only visual change.",'
                    '"assignments":[{"agent":"css","task":"Update button color."}],'
                    '"acceptance_criteria":["Button color is updated."],'
                    '"clarification_question":null,"rejection_reason":null}'
                ),
                latency_ms=25.0,
                token_usage=MODULE.TokenUsage(),
                model="groq/test-model",
            )

    monkeypatch.setattr(MODULE, "ManagerRouter", FakeRouter)

    trial = MODULE._run_case(MODULE.CASES[0], settings=object())

    assert len(calls) == 1
    assert calls[0].target_page == "index.html"
    assert trial["structural_validity"] is True
    assert trial["attempts"] == 1


def test_trial_from_result_preserves_empty_token_usage_report_shape():
    result = MODULE.RoutingEvidenceResult(
        case_id="css_case",
        request="Change button color.",
        expected_status="execute",
        expected_specialists=["css"],
        actual_status="execute",
        actual_specialists=["css"],
        routing_rationale="CSS owns presentation changes.",
        structurally_valid=True,
        routing_correct=True,
        latency_ms=100,
        token_usage=MODULE.TokenUsage(),
        validation_error=None,
    )

    trial = MODULE._trial_from_result(result)

    assert trial["token_usage"] == {}
