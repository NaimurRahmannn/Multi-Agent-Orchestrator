from types import SimpleNamespace

import pytest

from agentorchestra.config import GroqConfiguration, Settings
from agentorchestra.exceptions import SpecialistOutputError
from agentorchestra.models import EditRequest, SpecialistAssignment, SpecialistName
from agentorchestra.seo_models import SEOCompletion, SEOExecutionMode, SEOFinding
from agentorchestra.services.specialist_output import (
    extract_seo_completion,
    extract_specialist_completion,
)
from agentorchestra.services.specialist_runner import SpecialistRunner
from agentorchestra.services.workspace import cleanup_staged_workspace, create_staged_copy
from agentorchestra.specialist_models import SpecialistCompletion
from agentorchestra.tools import ProposePatchTool, ReadFileTool
from tests.test_workspace_service import make_settings


def completion(status="completed"):
    return SpecialistCompletion(
        status=status,
        summary="The specialist finished safely." if status == "completed" else "No patch applied.",
        remaining_issue=None if status == "completed" else "The exact target was unavailable.",
    )


def fake_agent_factory(specialist, recorder_ids=None, expected_model="test-model"):
    def factory(*, workspace, target_page, recorder, groq, verbose, mode=None):
        assert groq.model == expected_model
        assert verbose is False
        if recorder_ids is not None:
            recorder_ids.append(id(recorder))
        read_scope = (
            (target_page,)
            if specialist in {SpecialistName.HTML, SpecialistName.SEO}
            else tuple(sorted({target_page, "style.css"}))
        )
        patch_scope = (
            (target_page,)
            if specialist in {SpecialistName.HTML, SpecialistName.SEO}
            else ("style.css",)
        )
        tools = [ReadFileTool(handle=workspace, allowed_files=read_scope)]
        if mode is not SEOExecutionMode.DIAGNOSTIC:
            tools.append(
                ProposePatchTool(
                    handle=workspace,
                    specialist=specialist,
                    allowed_files=patch_scope,
                    recorder=recorder,
                )
            )
        return SimpleNamespace(tools=tools)

    return factory


def fake_task_factory(**kwargs):
    return SimpleNamespace(**kwargs)


def make_runner(specialist, executor, *, clock=None, recorder_ids=None):
    return SpecialistRunner(
        groq=GroqConfiguration(api_key="unit-test-secret", model="test-model"),
        agent_factories={specialist: fake_agent_factory(specialist, recorder_ids)},
        task_factories={specialist: fake_task_factory},
        crew_factory=lambda agent, task: SimpleNamespace(agent=agent, task=task),
        crew_executor=executor,
        clock=clock or iter([1.0, 1.01]).__next__,
    )


def run(
    runner,
    handle,
    specialist=SpecialistName.CSS,
    target_page="index.html",
    mode=SEOExecutionMode.EDIT,
):
    return runner.run_specialist(
        EditRequest(target_page=target_page, instruction="Apply one narrow edit."),
        SpecialistAssignment(agent=specialist, task="Apply one narrow edit."),
        ["The requested staged edit is present."],
        handle,
        mode=mode,
    )


def apply_css(crew, _inputs):
    crew.agent.tools[0]._run(file="style.css", start_line=1, end_line=8)
    crew.agent.tools[1]._run(
        file="style.css",
        old_text="  background: var(--accent);\n",
        new_text="  background: #0b3d91;\n",
        summary="Change button background.",
    )
    return completion()


def apply_html(crew, _inputs):
    crew.agent.tools[0]._run(file="index.html", start_line=1, end_line=11)
    crew.agent.tools[1]._run(
        file="index.html",
        old_text="  <h1>Home</h1>\n",
        new_text='  <h1 aria-label="Homepage">Home</h1>\n',
        summary="Add an accessible heading label.",
    )
    return completion()


def apply_seo(crew, _inputs):
    crew.agent.tools[0]._run(file="index.html", start_line=1, end_line=11)
    crew.agent.tools[1]._run(
        file="index.html",
        old_text="  <title>Home</title>\n",
        new_text="  <title>Harbor Light Web Design Studio</title>\n",
        summary="Improve the page title.",
    )
    return SEOCompletion(mode="edit", status="completed", summary="Updated page title.")


@pytest.mark.parametrize(
    ("specialist", "executor", "expected_file"),
    [
        (SpecialistName.HTML, apply_html, "index.html"),
        (SpecialistName.CSS, apply_css, "style.css"),
        (SpecialistName.SEO, apply_seo, "index.html"),
    ],
)
def test_applied_patch_produces_succeeded_from_actual_recorder(
    tmp_path, specialist, executor, expected_file
):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "runner-applied")

    result = run(make_runner(specialist, executor), handle, specialist)

    assert result.status == "succeeded"
    assert result.applied_patch_count == 1
    assert result.changed_files == [expected_file]
    assert result.patch_results[0].status == "applied"


def test_rejected_then_applied_still_succeeds_and_preserves_order(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "runner-corrected")

    def executor(crew, _inputs):
        patch = crew.agent.tools[1]
        patch._run(file="style.css", old_text="missing", new_text="new", summary="Miss.")
        apply_css(crew, _inputs)
        return completion()

    result = run(make_runner(SpecialistName.CSS, executor), handle)

    assert result.status == "succeeded"
    assert [item.status.value for item in result.patch_results] == ["rejected", "applied"]
    assert result.applied_patch_count == result.rejected_patch_count == 1


@pytest.mark.parametrize("old_text", ["missing", "}\n"])
def test_only_model_correctable_rejection_with_blocked_completion(tmp_path, old_text):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "runner-rejected")

    def executor(crew, _inputs):
        crew.agent.tools[1]._run(
            file="style.css", old_text=old_text, new_text="changed", summary="Reject."
        )
        return completion("blocked")

    result = run(make_runner(SpecialistName.CSS, executor), handle)

    assert result.status == "blocked"
    assert result.applied_patch_count == 0
    assert result.rejected_patch_count == 1


def test_blocked_completion_without_patch_is_blocked(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "runner-blocked")
    result = run(
        make_runner(SpecialistName.CSS, lambda crew, inputs: completion("blocked")),
        handle,
    )

    assert result.status == "blocked"
    assert result.patch_results == []


def test_completed_claim_without_recorder_evidence_is_failed(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "runner-no-evidence")
    result = run(
        make_runner(SpecialistName.CSS, lambda crew, inputs: completion()),
        handle,
    )

    assert result.status == "failed"
    assert "no applied patch evidence" in result.error


def test_crew_exception_is_failed_redacted_and_not_retried(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "runner-error")
    calls = []

    def executor(_crew, _inputs):
        calls.append(1)
        raise RuntimeError("provider leaked unit-test-secret")

    result = run(make_runner(SpecialistName.CSS, executor), handle)

    assert result.status == "failed"
    assert len(calls) == 1
    assert "unit-test-secret" not in result.error


def test_invalid_structured_output_is_failed(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "runner-output")
    result = run(
        make_runner(SpecialistName.CSS, lambda crew, inputs: {"status": "completed"}), handle
    )

    assert result.status == "failed"
    assert result.completion is None


def test_workspace_and_missing_target_fail_before_agent_invocation(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "runner-workspace")
    cleanup_staged_workspace(handle)
    calls = []
    result = run(
        make_runner(SpecialistName.CSS, lambda crew, inputs: calls.append(1)),
        handle,
    )
    assert result.status == "failed"
    assert calls == []

    valid = create_staged_copy(settings=settings, run_id_factory=lambda: "runner-missing")
    missing = run(
        make_runner(SpecialistName.CSS, lambda crew, inputs: calls.append(1)),
        valid,
        target_page="missing.html",
    )
    assert missing.status == "failed"
    assert calls == []


def test_latency_token_usage_and_absent_usage_are_normalized(tmp_path):
    settings = make_settings(tmp_path)
    first = create_staged_copy(settings=settings, run_id_factory=lambda: "runner-usage")

    def executor(crew, inputs):
        apply_css(crew, inputs)
        return SimpleNamespace(
            pydantic=completion(),
            token_usage={"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        )

    result = run(
        make_runner(SpecialistName.CSS, executor, clock=iter([5.0, 5.125]).__next__),
        first,
    )
    assert result.latency_ms == 125.0
    assert result.token_usage.total_tokens == 5

    second = create_staged_copy(settings=settings, run_id_factory=lambda: "runner-no-usage")
    assert run(make_runner(SpecialistName.CSS, apply_css), second).token_usage.total_tokens is None


def test_one_recorder_is_created_per_run(tmp_path):
    settings = make_settings(tmp_path)
    ids = []
    runner = make_runner(SpecialistName.CSS, apply_css, recorder_ids=ids, clock=lambda: 1.0)
    first = create_staged_copy(settings=settings, run_id_factory=lambda: "runner-recorder-one")
    second = create_staged_copy(settings=settings, run_id_factory=lambda: "runner-recorder-two")

    run(runner, first)
    run(runner, second)

    assert len(ids) == 2
    assert ids[0] != ids[1]


@pytest.mark.parametrize(
    ("specialist", "expected_key", "expected_model", "executor"),
    [
        (SpecialistName.HTML, "html-secret", "html-model", apply_html),
        (SpecialistName.CSS, "css-secret", "css-model", apply_css),
        (SpecialistName.SEO, "seo-secret", "seo-model", apply_seo),
    ],
)
def test_specialist_runner_selects_matching_role_credentials(
    tmp_path, specialist, expected_key, expected_model, executor
):
    base = make_settings(tmp_path)
    settings = Settings(
        project_root=base.project_root,
        groq_manager_api_key="manager-secret",
        groq_html_api_key="html-secret",
        groq_css_api_key="css-secret",
        groq_seo_api_key="seo-secret",
        groq_manager_model="manager-model",
        groq_html_model="html-model",
        groq_css_model="css-model",
        groq_seo_model="seo-model",
    )
    handle = create_staged_copy(
        settings=settings, run_id_factory=lambda: f"runner-key-{specialist.value}"
    )
    base_factory = fake_agent_factory(specialist, expected_model=expected_model)
    captured = []

    def recording_factory(**kwargs):
        captured.append((kwargs["groq"].api_key, kwargs["groq"].model))
        return base_factory(**kwargs)

    runner = SpecialistRunner(
        settings=settings,
        agent_factories={specialist: recording_factory},
        task_factories={specialist: fake_task_factory},
        crew_factory=lambda agent, task: SimpleNamespace(agent=agent, task=task),
        crew_executor=executor,
        clock=iter([1.0, 1.01]).__next__,
    )

    result = run(runner, handle, specialist)

    assert result.status == "succeeded"
    assert captured == [(expected_key, expected_model)]


def test_seo_diagnostic_returns_findings_without_patch_evidence(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "runner-seo")
    finding = SEOFinding(
        code="missing_description",
        severity="warning",
        title="Meta description is missing",
        source_file="index.html",
        evidence="No meta description element appears in the selected source.",
        recommendation="Add one concise meta description.",
    )
    runner = make_runner(
        SpecialistName.SEO,
        lambda crew, inputs: SEOCompletion(
            mode="diagnostic",
            status="completed",
            summary="Reviewed source SEO.",
            findings=[finding],
        ),
    )

    result = run(runner, handle, SpecialistName.SEO, mode=SEOExecutionMode.DIAGNOSTIC)

    assert result.status == "succeeded"
    assert result.patch_results == []
    assert result.completion.findings == [finding]
    assert extract_seo_completion(result.completion) == result.completion


def test_output_extraction_supports_public_shapes_and_strictly_rejects_invalid():
    native = completion()
    assert extract_specialist_completion(native) == native
    assert extract_specialist_completion(native.model_dump()) == native
    assert extract_specialist_completion(SimpleNamespace(pydantic=native)) == native
    assert extract_specialist_completion(SimpleNamespace(json_dict=native.model_dump())) == native
    assert extract_specialist_completion(SimpleNamespace(raw=native.model_dump_json())) == native
    assert (
        extract_specialist_completion(
            SimpleNamespace(tasks_output=[SimpleNamespace(pydantic=native)])
        )
        == native
    )
    with pytest.raises(SpecialistOutputError):
        extract_specialist_completion(None)
    with pytest.raises(SpecialistOutputError):
        extract_specialist_completion("not-json unit-test-secret")
    with pytest.raises(SpecialistOutputError) as error:
        extract_specialist_completion({"status": "completed", "summary": "Done", "extra": "secret"})
    assert "unit-test-secret" not in str(error.value)
