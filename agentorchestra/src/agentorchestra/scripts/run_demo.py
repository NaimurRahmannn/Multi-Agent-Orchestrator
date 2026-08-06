from __future__ import annotations

import argparse
import importlib.util
from collections.abc import Callable, Sequence

from pydantic import ValidationError

from agentorchestra.config import Settings, get_settings
from agentorchestra.exceptions import AgentOrchestraError, PromotionRollbackError
from agentorchestra.flow import AgentOrchestraFlow, build_production_flow_dependencies
from agentorchestra.models import EditRequest
from agentorchestra.pipeline_models import EditRunReport
from agentorchestra.scripts.run_edit_flow import (
    CRITICAL_RECOVERY_EXIT_CODE,
    EXIT_CODES,
    print_edit_run_report,
)
from agentorchestra.scripts.specialist_cli_support import redact_cli_error
from agentorchestra.services.promotion import reset_working_from_fixture
from agentorchestra.services.ui_support import check_runtime_readiness
from agentorchestra.services.workspace import validate_site_structure

DEFAULT_INSTRUCTION = (
    "In index.html, add a concise meta description based on the existing page content. "
    "In style.css, inside the .hero-copy h1 rule, change font-size from 3rem to 3.4rem. "
    "Do not change anything else."
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check or run the final AgentOrchestra demo.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Run non-mutating readiness checks.")
    mode.add_argument("--run", action="store_true", help="Run the production edit Flow.")
    parser.add_argument("--apply", action="store_true", help="Required with --run.")
    parser.add_argument("--target-page", default="index.html")
    parser.add_argument("--instruction", default=DEFAULT_INSTRUCTION)
    parser.add_argument("--reset-first", action="store_true")
    parser.add_argument("--reset-after", action="store_true")
    return parser


def readiness_checks(settings: Settings) -> dict[str, bool]:
    """Return boolean-only demo readiness without making external service calls."""
    runtime = check_runtime_readiness(settings, check_chromium=True)
    checks = runtime.model_dump(mode="python")
    try:
        validate_site_structure(settings.fixture_site_dir)
        checks["fixture_site_valid"] = True
    except Exception:
        checks["fixture_site_valid"] = False
    staging_entries = [item for item in settings.staging_root_dir.iterdir() if item.name != ".gitkeep"]
    checks["staging_clean"] = not staging_entries
    transaction_paths = list(settings.project_root.glob(".agentorchestra-*-candidate-*"))
    transaction_paths += list(settings.project_root.glob(".agentorchestra-*-backup-*"))
    transaction_paths += list(settings.project_root.glob("sites/.agentorchestra-*-candidate-*"))
    transaction_paths += list(settings.project_root.glob("sites/.agentorchestra-*-backup-*"))
    checks["transaction_paths_clean"] = not transaction_paths
    checks["python_runtime_available"] = True
    checks["crewai_importable"] = importlib.util.find_spec("crewai") is not None
    checks["streamlit_importable"] = importlib.util.find_spec("streamlit") is not None
    return {name: bool(value) for name, value in checks.items()}


def print_readiness(checks: dict[str, bool]) -> bool:
    print("demo readiness:")
    for name, passed in checks.items():
        print(f"- {name}: {'ready' if passed else 'missing'}")
    ready = all(checks.values())
    print(f"overall: {'ready' if ready else 'not ready'}")
    return ready


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    flow: AgentOrchestraFlow | None = None,
    readiness_checker: Callable[[Settings], dict[str, bool]] = readiness_checks,
    resetter: Callable[..., object] = reset_working_from_fixture,
) -> int:
    args = build_parser().parse_args(argv)
    resolved = settings or get_settings()
    if args.check:
        return 0 if print_readiness(readiness_checker(resolved)) else 1
    if not args.apply:
        print("No demo was run. Re-run --run with --apply to allow live calls and mutation.")
        return 2

    checks = readiness_checker(resolved)
    if not print_readiness(checks):
        print("Demo stopped because mandatory readiness checks failed.")
        return 1

    try:
        if args.reset_first:
            resetter(settings=resolved)
            print("reset first: complete")
        request = EditRequest(target_page=args.target_page, instruction=args.instruction)
        production_flow = flow or AgentOrchestraFlow(
            dependencies=build_production_flow_dependencies(settings=resolved)
        )
        payload = production_flow.kickoff(inputs={"request": request.model_dump(mode="json")})
        report = EditRunReport.model_validate(payload)
        print_edit_run_report(report, resolved)
        for screenshot in report.screenshots:
            print(f"screenshot {screenshot.kind.value}: {screenshot.status.value}")
        if report.metrics is not None:
            total = report.metrics.total_token_usage
            token_state = total.total_tokens if total.total_tokens is not None else "unavailable"
            print(f"run tokens: {token_state}")
        return EXIT_CODES[report.status]
    except PromotionRollbackError as exc:
        print("Critical: working-site recovery is required")
        print(f"error: {redact_cli_error(str(exc), resolved)}")
        if exc.recovery_paths:
            print(f"recovery paths: {', '.join(exc.recovery_paths)}")
        return CRITICAL_RECOVERY_EXIT_CODE
    except (AgentOrchestraError, ValidationError, ValueError) as exc:
        print(f"Demo failed: {redact_cli_error(str(exc), resolved)}")
        return 1
    finally:
        if args.reset_after:
            try:
                resetter(settings=resolved)
                print("reset after: complete")
            except PromotionRollbackError:
                raise
            except AgentOrchestraError as exc:
                print(f"reset after failed: {redact_cli_error(str(exc), resolved)}")


if __name__ == "__main__":
    raise SystemExit(main())
