from __future__ import annotations

import argparse
from collections.abc import Sequence

from agentorchestra.config import Settings, get_settings
from agentorchestra.exceptions import AgentOrchestraError, PromotionError, PromotionRollbackError
from agentorchestra.scripts.specialist_cli_support import redact_cli_error
from agentorchestra.services.promotion import reset_working_from_fixture

CRITICAL_RECOVERY_EXIT_CODE = 9


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reset sites/working from sites/fixture using controlled replacement."
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Required to replace the working sample site with the fixture copy.",
    )
    return parser


def main(argv: Sequence[str] | None = None, *, settings: Settings | None = None) -> int:
    args = build_parser().parse_args(argv)
    resolved_settings = settings or get_settings()
    if not args.reset:
        print("No reset was run. Re-run with --reset to restore sites/working from fixture.")
        return 2
    try:
        result = reset_working_from_fixture(settings=resolved_settings)
    except PromotionRollbackError as exc:
        print("reset status: critical")
        print("Critical: working-site recovery is required")
        print(f"error: {redact_cli_error(str(exc), resolved_settings)}")
        if exc.recovery_paths:
            print(f"recovery paths: {', '.join(exc.recovery_paths)}")
        return CRITICAL_RECOVERY_EXIT_CODE
    except PromotionError as exc:
        print("reset status: failed")
        print(f"working restored: {'yes' if exc.working_restored else 'no'}")
        print(f"error: {redact_cli_error(str(exc), resolved_settings)}")
        return 1
    except AgentOrchestraError as exc:
        print("reset status: failed")
        print(f"error: {redact_cli_error(str(exc), resolved_settings)}")
        return 1
    print("reset status: succeeded")
    print(f"reset transaction: {result.status.value}")
    print(f"working reset: {'yes' if result.working_reset else 'no'}")
    print(f"working matches fixture: {'yes' if result.working_matches_fixture else 'no'}")
    print(f"temporary candidate cleaned: {'yes' if result.candidate_cleaned else 'no'}")
    print(f"temporary backup cleaned: {'yes' if result.backup_cleaned else 'no'}")
    print(f"final content digest: {result.final_working_digest}")
    for warning in result.warnings:
        print(f"cleanup warning: {warning}")
    return 0 if result.working_matches_fixture else 1


if __name__ == "__main__":
    raise SystemExit(main())
