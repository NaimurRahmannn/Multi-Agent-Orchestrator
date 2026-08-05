from agentorchestra.config import Settings
from agentorchestra.models import SpecialistName, TokenUsage
from agentorchestra.scripts import run_specialist
from agentorchestra.seo_models import SEOCompletion, SEOFinding
from agentorchestra.services.specialist_execution import SpecialistExecutionService
from agentorchestra.services.workspace import (
    cleanup_staged_workspace,
    generate_diff,
    get_workspace_handle,
    propose_patch,
)
from agentorchestra.specialist_models import SpecialistExecutionReport, SpecialistRunResult
from tests.specialist_helpers import run_result
from tests.test_specialist_execution import ScriptedRunner
from tests.test_workspace_service import make_settings


def live_settings(tmp_path):
    base = make_settings(tmp_path)
    return Settings(
        project_root=base.project_root,
        groq_manager_api_key="manager-unit-test-secret",
        groq_html_api_key="html-unit-test-secret",
        groq_css_api_key="css-unit-test-secret",
        groq_seo_api_key="seo-unit-test-secret",
        groq_manager_model="manager-test-model",
        groq_html_model="html-test-model",
        groq_css_model="css-test-model",
        groq_seo_model="seo-test-model",
    )


def staged_runs(settings):
    if not settings.staging_root_dir.exists():
        return []
    return sorted(path for path in settings.staging_root_dir.iterdir() if path.is_dir())


def service(settings, statuses):
    return SpecialistExecutionService(settings=settings, runner=ScriptedRunner(statuses))


def test_single_specialist_cli_html_and_css_display_and_cleanup(tmp_path, capsys):
    for index, specialist in enumerate(("html", "css")):
        settings = live_settings(tmp_path / str(index))
        code = run_specialist.main(
            [
                "--specialist",
                specialist,
                "--target-page",
                "index.html",
                "--task",
                f"Perform {specialist} edit.",
            ],
            settings=settings,
            execution_service=service(settings, ["succeeded"]),
        )
        output = capsys.readouterr().out

        assert code == 0
        assert f"specialist: {specialist}" in output
        assert "runtime status: succeeded" in output
        assert "patch 1: status=applied" in output
        assert "final unified diff:" in output
        assert "working unchanged: yes" in output
        assert "fixture unchanged: yes" in output
        assert "staging cleanup: complete" in output
        assert staged_runs(settings) == []


def test_single_specialist_cli_supports_seo_edit(tmp_path, capsys):
    settings = live_settings(tmp_path)
    code = run_specialist.main(
        [
            "--specialist",
            "seo",
            "--mode",
            "edit",
            "--target-page",
            "index.html",
            "--task",
            "Improve the page title.",
        ],
        settings=settings,
        execution_service=service(settings, ["succeeded"]),
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "specialist: seo" in output
    assert "patch 1: status=applied" in output
    assert staged_runs(settings) == []


def test_single_specialist_cli_supports_read_only_seo_diagnostic(tmp_path, capsys):
    settings = live_settings(tmp_path)

    class DiagnosticService:
        def execute(self, request, plan, workspace):
            completion = SEOCompletion(
                mode="diagnostic",
                status="completed",
                summary="Reviewed source SEO.",
                findings=[
                    SEOFinding(
                        code="missing_description",
                        severity="warning",
                        title="Meta description missing",
                        source_file=request.target_page,
                        evidence="No meta description is present.",
                        recommendation="Add one concise description.",
                    )
                ],
            )
            result = SpecialistRunResult(
                specialist="seo",
                mode="diagnostic",
                assignment=plan.assignments[0].task,
                status="succeeded",
                completion=completion,
                patch_results=[],
                changed_files=[],
                applied_patch_count=0,
                rejected_patch_count=0,
                latency_ms=1.0,
                token_usage=TokenUsage(),
                model="groq/seo",
            )
            return SpecialistExecutionReport(
                run_id=workspace.run_id,
                request=request,
                plan=plan,
                status="succeeded",
                results=[result],
                diff_report=generate_diff(workspace, settings=settings),
                total_latency_ms=1.0,
                stopped_early=False,
                seo_mode="diagnostic",
            )

    code = run_specialist.main(
        [
            "--specialist",
            "seo",
            "--mode",
            "diagnostic",
            "--target-page",
            "index.html",
            "--task",
            "Diagnose source SEO.",
        ],
        settings=settings,
        execution_service=DiagnosticService(),
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "seo finding [warning] missing_description" in output
    assert "diff changed files: none" in output
    assert staged_runs(settings) == []


def test_html_and_css_cli_reject_diagnostic_before_staging(tmp_path, capsys):
    settings = live_settings(tmp_path)
    for specialist in ("html", "css"):
        code = run_specialist.main(
            [
                "--specialist",
                specialist,
                "--mode",
                "diagnostic",
                "--target-page",
                "index.html",
                "--task",
                "Diagnose.",
            ],
            settings=settings,
            execution_service=service(settings, []),
        )
        assert code == 1
        assert "supported only by the SEO specialist" in capsys.readouterr().out
    assert staged_runs(settings) == []


def test_single_specialist_cli_requires_only_selected_agent_key(tmp_path, capsys):
    base = make_settings(tmp_path)
    settings = Settings(
        project_root=base.project_root,
        groq_css_api_key="css-only-secret",
        groq_css_model="css-only-model",
    )

    code = run_specialist.main(
        [
            "--specialist",
            "css",
            "--target-page",
            "index.html",
            "--task",
            "Perform css edit.",
        ],
        settings=settings,
        execution_service=service(settings, ["succeeded"]),
    )

    assert code == 0
    assert "css-only-secret" not in capsys.readouterr().out
    assert staged_runs(settings) == []


def test_single_cli_displays_rejected_then_applied_evidence(tmp_path, capsys):
    settings = live_settings(tmp_path)

    class EvidenceRunner:
        def run_specialist(self, request, assignment, acceptance_criteria, workspace):
            rejected = propose_patch(
                workspace,
                specialist=SpecialistName.CSS,
                file="style.css",
                old_text="missing",
                new_text="new",
                summary="Rejected attempt.",
                allowed_files=("style.css",),
            )
            applied = propose_patch(
                workspace,
                specialist=SpecialistName.CSS,
                file="style.css",
                old_text="  background: var(--accent);\n",
                new_text="  background: #0b3d91;\n",
                summary="Applied attempt.",
                allowed_files=("style.css",),
            )
            return run_result("css", assignment=assignment.task, patches=[rejected, applied])

    code = run_specialist.main(
        [
            "--specialist",
            "css",
            "--target-page",
            "index.html",
            "--task",
            "Perform css edit.",
        ],
        settings=settings,
        execution_service=SpecialistExecutionService(settings=settings, runner=EvidenceRunner()),
    )
    output = capsys.readouterr().out

    assert code == 0
    assert "patch 1: status=rejected" in output
    assert "patch 2: status=applied" in output


def test_single_cli_blocked_and_failed_return_nonzero_and_redact(tmp_path, capsys):
    for index, status in enumerate(("blocked", "failed")):
        settings = live_settings(tmp_path / str(index))
        code = run_specialist.main(
            [
                "--specialist",
                "css",
                "--target-page",
                "index.html",
                "--task",
                "Perform css edit.",
            ],
            settings=settings,
            execution_service=service(settings, [status]),
        )
        output = capsys.readouterr().out
        assert code == 1
        assert f"runtime status: {status}" in output
        assert "css-unit-test-secret" not in output
        assert "chain-of-thought" not in output.casefold()
        assert staged_runs(settings) == []


def test_single_cli_keep_staging_is_explicit_and_project_relative(tmp_path, capsys):
    settings = live_settings(tmp_path)
    code = run_specialist.main(
        [
            "--specialist",
            "css",
            "--target-page",
            "index.html",
            "--task",
            "Perform css edit.",
            "--keep-staging",
        ],
        settings=settings,
        execution_service=service(settings, ["succeeded"]),
    )
    output = capsys.readouterr().out
    runs = staged_runs(settings)

    assert code == 0
    assert len(runs) == 1
    assert f"staging preserved: sites/staging/{runs[0].name}" in output
    assert str(settings.project_root) not in output
    cleanup_staged_workspace(get_workspace_handle(runs[0].name, settings=settings))


def test_single_cli_validates_arguments_without_creating_staging(tmp_path, capsys):
    settings = live_settings(tmp_path)
    code = run_specialist.main(
        [
            "--specialist",
            "html",
            "--target-page",
            "../index.html",
            "--task",
            "Edit it.",
        ],
        settings=settings,
        execution_service=service(settings, []),
    )

    assert code == 1
    assert "Specialist preview failed:" in capsys.readouterr().out
    assert staged_runs(settings) == []
