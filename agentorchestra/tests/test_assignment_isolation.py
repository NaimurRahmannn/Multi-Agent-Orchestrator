from types import SimpleNamespace

from agentorchestra.config import GroqConfiguration
from agentorchestra.models import (
    HTML_CSS_EDIT_REQUEST_TYPE,
    EditRequest,
    ManagerRoutingPlan,
    SpecialistAssignment,
    SpecialistName,
)
from agentorchestra.prompts.specialists import build_specialist_task_description
from agentorchestra.services.specialist_execution import SpecialistExecutionService
from agentorchestra.services.specialist_runner import SpecialistRunner
from agentorchestra.services.workspace import create_staged_copy
from agentorchestra.specialist_models import SpecialistCompletion
from agentorchestra.tools import PatchEvidenceRecorder, ProposePatchTool, ReadFileTool
from tests.test_workspace_service import make_settings


def test_combined_html_css_request_keeps_each_specialist_on_its_assignment(tmp_path):
    settings = make_settings(tmp_path)
    page = settings.working_site_dir / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "<h1>Home</h1>",
            "<h1>Simple websites for neighborhood teams</h1>",
        ),
        encoding="utf-8",
    )
    stylesheet = settings.working_site_dir / "style.css"
    stylesheet.write_text(
        stylesheet.read_text(encoding="utf-8").replace(
            "background: var(--accent);",
            "background: green;",
        ),
        encoding="utf-8",
    )
    workspace = create_staged_copy(settings=settings, run_id_factory=lambda: "assignment-split")
    request = EditRequest(
        target_page="index.html",
        instruction=(
            "Change Simple websites for neighborhood teams to simple websites and change "
            "the Start a project green button to red."
        ),
    )
    html_assignment = SpecialistAssignment(
        agent=SpecialistName.HTML,
        task=(
            "Change the text Simple websites for neighborhood teams to simple websites."
        ),
    )
    css_assignment = SpecialistAssignment(
        agent=SpecialistName.CSS,
        task="Change the Start a project button background from green to red.",
    )
    criteria = [
        "The home page heading is simple websites.",
        "The Start a project button background is red.",
    ]
    plan = ManagerRoutingPlan(
        status="execute",
        request_type=HTML_CSS_EDIT_REQUEST_TYPE,
        selected_specialists=[SpecialistName.HTML, SpecialistName.CSS],
        routing_rationale="HTML owns text and CSS owns presentation.",
        assignments=[html_assignment, css_assignment],
        acceptance_criteria=criteria,
    )

    def html_agent_factory(*, workspace, target_page, recorder, **kwargs):
        del kwargs
        return SimpleNamespace(
            tools=[
                ReadFileTool(handle=workspace, allowed_files=(target_page,)),
                ProposePatchTool(
                    handle=workspace,
                    specialist=SpecialistName.HTML,
                    allowed_files=(target_page,),
                    recorder=recorder,
                ),
            ]
        )

    def html_task_factory(*, agent, request, assignment, acceptance_criteria):
        return SimpleNamespace(
            agent=agent,
            description=build_specialist_task_description(
                specialist=SpecialistName.HTML,
                request=request,
                assignment=assignment,
                acceptance_criteria=acceptance_criteria,
                allowed_read_files=(request.target_page,),
                allowed_patch_files=(request.target_page,),
            ),
        )

    def html_executor(crew, inputs):
        assert inputs == {
            "target_page": "index.html",
            "assignment": html_assignment.task,
        }
        assert html_assignment.task in crew.task.description
        assert request.instruction not in crew.task.description
        assert criteria[1] not in crew.task.description
        crew.agent.tools[0]._run(file="index.html", start_line=1, end_line=12)
        crew.agent.tools[1]._run(
            file="index.html",
            old_text="<h1>Simple websites for neighborhood teams</h1>",
            new_text="<h1>simple websites</h1>",
            summary="Change only the assigned heading text.",
        )
        return SpecialistCompletion(
            status="completed",
            summary="Changed the assigned heading text.",
        )

    runner = SpecialistRunner(
        settings=settings,
        groq=GroqConfiguration(api_key="unit-test-secret", model="test-model"),
        agent_factories={SpecialistName.HTML: html_agent_factory},
        task_factories={SpecialistName.HTML: html_task_factory},
        crew_factory=lambda agent, task: SimpleNamespace(agent=agent, task=task),
        crew_executor=html_executor,
        recorder_factory=PatchEvidenceRecorder,
    )
    report = SpecialistExecutionService(settings=settings, runner=runner).execute(
        request,
        plan,
        workspace,
    )

    assert report.status.value == "succeeded"
    assert [result.specialist for result in report.results] == [
        SpecialistName.HTML,
        SpecialistName.CSS,
    ]
    assert all(result.status.value == "succeeded" for result in report.results)
    assert report.results[0].rejected_patch_count == 0
    assert report.results[1].model == "deterministic/css-semantic-v1"
    assert report.diff_report.changed_files == ["index.html", "style.css"]
    assert "<h1>simple websites</h1>" in (workspace.path / "index.html").read_text()
    assert "background: #dc2626;" in (workspace.path / "style.css").read_text()
