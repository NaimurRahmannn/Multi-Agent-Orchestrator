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
