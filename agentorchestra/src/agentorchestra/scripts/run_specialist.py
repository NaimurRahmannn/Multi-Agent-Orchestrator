from __future__ import annotations

import argparse
from collections.abc import Sequence

from pydantic import ValidationError

from agentorchestra.config import Settings, get_settings
from agentorchestra.exceptions import AgentOrchestraError
from agentorchestra.models import (
    EditRequest,
    ManagerRoutingPlan,
    RoutingStatus,
    SpecialistAssignment,
    SpecialistName,
)
from agentorchestra.scripts.specialist_cli_support import (
    print_execution_report,
    redact_cli_error,
    tree_digest,
)
from agentorchestra.services.specialist_execution import SpecialistExecutionService
from agentorchestra.services.specialist_runner import SpecialistRunner
from agentorchestra.services.workspace import cleanup_staged_workspace, create_staged_copy
from agentorchestra.specialist_models import SpecialistExecutionStatus


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one staged HTML or CSS specialist edit.")
    parser.add_argument("--specialist", required=True, choices=("html", "css"))
    parser.add_argument("--target-page", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--keep-staging", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    execution_service: SpecialistExecutionService | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    resolved_settings = settings or get_settings()
    handle = None
    exit_code = 1
    try:
        resolved_settings.require_groq_configuration()
        specialist = SpecialistName(args.specialist)
        request = EditRequest(target_page=args.target_page, instruction=args.task)
        assignment = SpecialistAssignment(agent=specialist, task=args.task)
        plan = ManagerRoutingPlan(
            status=RoutingStatus.EXECUTE,
            request_type=f"manual_{specialist.value}_specialist",
            selected_specialists=[specialist],
            routing_rationale="Manual single-specialist preview selected by explicit CLI argument.",
            assignments=[assignment],
            acceptance_criteria=["The requested narrow edit is applied in the approved staged file."],
            clarification_question=None,
            rejection_reason=None,
        )
        working_before = tree_digest(resolved_settings.working_site_dir)
        fixture_before = tree_digest(resolved_settings.fixture_site_dir)
        handle = create_staged_copy(settings=resolved_settings)
        service = execution_service or SpecialistExecutionService(
            settings=resolved_settings,
            runner=SpecialistRunner(settings=resolved_settings, verbose=args.verbose),
        )
        report = service.execute(request, plan, handle)
        print_execution_report(report, resolved_settings)
        working_unchanged = tree_digest(resolved_settings.working_site_dir) == working_before
        fixture_unchanged = tree_digest(resolved_settings.fixture_site_dir) == fixture_before
        print(f"working unchanged: {'yes' if working_unchanged else 'no'}")
        print(f"fixture unchanged: {'yes' if fixture_unchanged else 'no'}")
        if not working_unchanged or not fixture_unchanged:
            raise AgentOrchestraError("A protected site tree changed during specialist execution.")
        exit_code = 0 if report.status is SpecialistExecutionStatus.SUCCEEDED else 1
    except (AgentOrchestraError, ValidationError, ValueError) as exc:
        print(f"Specialist preview failed: {redact_cli_error(str(exc), resolved_settings)}")
        exit_code = 1
    finally:
        if handle is not None:
            if args.keep_staging:
                print(f"staging preserved: sites/staging/{handle.run_id}")
            else:
                try:
                    cleanup_staged_workspace(handle)
                    print("staging cleanup: complete")
                except AgentOrchestraError as exc:
                    print(f"staging cleanup failed: {redact_cli_error(str(exc), resolved_settings)}")
                    exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
