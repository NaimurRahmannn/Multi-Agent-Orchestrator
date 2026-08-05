import pytest

from agentorchestra.exceptions import SpecialistPlanError, UnsupportedSpecialistError
from agentorchestra.models import EditRequest, ManagerRoutingPlan, SpecialistName
from agentorchestra.services.specialist_execution import SpecialistExecutionService
from agentorchestra.services.workspace import (
    cleanup_staged_workspace,
    create_staged_copy,
    propose_patch,
)
from tests.specialist_helpers import execute_plan, run_result
from tests.test_workspace_service import make_settings


class ScriptedRunner:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = []

    def run_specialist(self, request, assignment, acceptance_criteria, workspace):
        self.calls.append(assignment.agent)
        status = self.statuses.pop(0)
        patches = []
        if status == "succeeded":
            if assignment.agent is SpecialistName.HTML:
                patches.append(
                    propose_patch(
                        workspace,
                        specialist=SpecialistName.HTML,
                        file=request.target_page,
                        old_text="  <h1>Home</h1>\n",
                        new_text='  <h1 data-stage="edited">Home</h1>\n',
                        summary="Apply HTML edit.",
                        allowed_files=(request.target_page,),
                    )
                )
            else:
                patches.append(
                    propose_patch(
                        workspace,
                        specialist=SpecialistName.CSS,
                        file="style.css",
                        old_text="  background: var(--accent);\n",
                        new_text="  background: #0b3d91;\n",
                        summary="Apply CSS edit.",
                        allowed_files=("style.css",),
                    )
                )
        return run_result(
            assignment.agent,
            status,
            assignment=assignment.task,
            patches=patches,
        )


def execute(tmp_path, specialists, statuses):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "execution")
    runner = ScriptedRunner(statuses)
    report = SpecialistExecutionService(settings=settings, runner=runner).execute(
        EditRequest(target_page="index.html", instruction="Apply the requested edits."),
        execute_plan(*specialists),
        handle,
    )
    return settings, handle, runner, report


@pytest.mark.parametrize(
    ("specialists", "expected_files"),
    [
        (("html",), ["index.html"]),
        (("css",), ["style.css"]),
        (("html", "css"), ["index.html", "style.css"]),
        (("css", "html"), ["style.css", "index.html"]),
    ],
)
def test_valid_plans_run_only_selected_specialists_in_plan_order(
    tmp_path, specialists, expected_files
):
    settings, handle, runner, report = execute(
        tmp_path, specialists, ["succeeded"] * len(specialists)
    )

    assert [item.value for item in runner.calls] == list(specialists)
    assert [item.specialist.value for item in report.results] == list(specialists)
    assert report.status == "succeeded"
    assert set(report.diff_report.changed_files) == set(expected_files)
    assert handle.path.exists()
    assert (settings.working_site_dir / "index.html").read_text(encoding="utf-8").find("data-stage") < 0


@pytest.mark.parametrize("status", ["clarification_required", "out_of_scope"])
def test_non_execute_plan_invokes_no_specialist_and_does_not_mutate_staging(tmp_path, status):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "non-execute")
    runner = ScriptedRunner([])
    plan = ManagerRoutingPlan(
        status=status,
        request_type="non_execute",
        selected_specialists=[],
        routing_rationale="The request cannot execute.",
        assignments=[],
        acceptance_criteria=[],
        clarification_question="What should change?" if status == "clarification_required" else None,
        rejection_reason="Unsupported." if status == "out_of_scope" else None,
    )

    with pytest.raises(SpecialistPlanError):
        SpecialistExecutionService(settings=settings, runner=runner).execute(
            EditRequest(target_page="index.html", instruction="Request."), plan, handle
        )

    assert runner.calls == []


def test_seo_plan_is_rejected_before_runner_or_staged_mutation(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "seo-plan")
    before = (handle.path / "index.html").read_bytes()
    runner = ScriptedRunner([])

    with pytest.raises(UnsupportedSpecialistError):
        SpecialistExecutionService(settings=settings, runner=runner).execute(
            EditRequest(target_page="index.html", instruction="Improve SEO."),
            execute_plan("seo"),
            handle,
        )

    assert runner.calls == []
    assert (handle.path / "index.html").read_bytes() == before


def test_invalid_workspace_and_missing_target_are_rejected_before_runner(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "invalid-workspace")
    cleanup_staged_workspace(handle)
    runner = ScriptedRunner([])
    service = SpecialistExecutionService(settings=settings, runner=runner)

    with pytest.raises(SpecialistPlanError):
        service.execute(
            EditRequest(target_page="index.html", instruction="Edit."), execute_plan("css"), handle
        )

    valid = create_staged_copy(settings=settings, run_id_factory=lambda: "missing-target")
    with pytest.raises(SpecialistPlanError):
        service.execute(
            EditRequest(target_page="missing.html", instruction="Edit."), execute_plan("css"), valid
        )
    assert runner.calls == []


@pytest.mark.parametrize(
    ("specialists", "statuses", "overall", "stopped"),
    [
        (("html", "css"), ("blocked", "succeeded"), "blocked", True),
        (("html", "css"), ("failed", "succeeded"), "failed", True),
        (("html", "css"), ("succeeded", "blocked"), "partial", False),
        (("html", "css"), ("succeeded", "failed"), "partial", False),
        (("css",), ("blocked",), "blocked", False),
        (("css",), ("failed",), "failed", False),
    ],
)
def test_stop_policy_and_overall_status(tmp_path, specialists, statuses, overall, stopped):
    _settings, _handle, runner, report = execute(tmp_path, specialists, statuses)

    assert report.status == overall
    assert report.stopped_early is stopped
    expected_calls = 1 if stopped else len(specialists)
    assert len(runner.calls) == expected_calls


def test_final_diff_is_authoritative_and_protected_trees_remain_unchanged(tmp_path):
    settings = make_settings(tmp_path)
    working_before = (settings.working_site_dir / "style.css").read_bytes()
    fixture_before = (settings.fixture_site_dir / "style.css").read_bytes()
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "authoritative-diff")
    runner = ScriptedRunner(["succeeded"])
    report = SpecialistExecutionService(settings=settings, runner=runner).execute(
        EditRequest(target_page="index.html", instruction="Change the button."),
        execute_plan("css"),
        handle,
    )

    assert report.results[0].patch_results[0].status == "applied"
    assert report.diff_report.changed_files == ["style.css"]
    assert "+  background: #0b3d91;" in report.diff_report.combined_diff
    assert (settings.working_site_dir / "style.css").read_bytes() == working_before
    assert (settings.fixture_site_dir / "style.css").read_bytes() == fixture_before
    assert handle.path.exists()
