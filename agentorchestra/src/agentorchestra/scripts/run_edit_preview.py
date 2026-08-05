from __future__ import annotations

import argparse
from collections.abc import Sequence

from pydantic import ValidationError

from agentorchestra.agents.manager import ManagerRouter, ManagerRoutingInterface
from agentorchestra.config import GroqAgentName, Settings, get_settings
from agentorchestra.exceptions import AgentOrchestraError
from agentorchestra.models import EditRequest, RoutingStatus
from agentorchestra.scripts.specialist_cli_support import (
    print_execution_report,
    print_manager_plan,
    redact_cli_error,
    tree_digest,
)
from agentorchestra.services.specialist_execution import SpecialistExecutionService
from agentorchestra.services.specialist_runner import SpecialistRunner
from agentorchestra.services.workspace import cleanup_staged_workspace, create_staged_copy
from agentorchestra.specialist_models import SpecialistExecutionStatus


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Route and preview a staged HTML/CSS edit without QA or promotion."
    )
    parser.add_argument("--target-page", required=True)
    parser.add_argument("--instruction", required=True)
    parser.add_argument("--keep-staging", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    router: ManagerRoutingInterface | None = None,
    execution_service: SpecialistExecutionService | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    resolved_settings = settings or get_settings()
    handle = None
    exit_code = 1
    try:
        resolved_settings.require_groq_configuration(GroqAgentName.MANAGER)
        request = EditRequest(target_page=args.target_page, instruction=args.instruction)
        manager_result = (router or ManagerRouter(settings=resolved_settings)).route(request)
        plan = manager_result.plan
        print_manager_plan(plan, resolved_settings)
        if plan.status is RoutingStatus.CLARIFICATION_REQUIRED:
            print(
                "clarification question: "
                f"{redact_cli_error(plan.clarification_question or '', resolved_settings)}"
            )
            return 2
        if plan.status is RoutingStatus.OUT_OF_SCOPE:
            print(
                f"rejection reason: {redact_cli_error(plan.rejection_reason or '', resolved_settings)}"
            )
            return 2
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
            raise AgentOrchestraError("A protected site tree changed during routed execution.")
        exit_code = 0 if report.status is SpecialistExecutionStatus.SUCCEEDED else 1
    except (AgentOrchestraError, ValidationError, ValueError) as exc:
        print(f"Edit preview failed: {redact_cli_error(str(exc), resolved_settings)}")
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
                    print(
                        f"staging cleanup failed: {redact_cli_error(str(exc), resolved_settings)}"
                    )
                    exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
