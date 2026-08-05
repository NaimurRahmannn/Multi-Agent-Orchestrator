from __future__ import annotations

import argparse
from collections.abc import Sequence

from pydantic import ValidationError

from agentorchestra.config import Settings, get_settings
from agentorchestra.exceptions import AgentOrchestraError, PromotionRollbackError
from agentorchestra.flow import AgentOrchestraFlow
from agentorchestra.models import EditRequest
from agentorchestra.pipeline_models import EditOutcomeStatus, EditRunReport
from agentorchestra.scripts.specialist_cli_support import redact_cli_error

EXIT_CODES = {
    EditOutcomeStatus.ACCEPTED: 0,
    EditOutcomeStatus.DIAGNOSTIC_COMPLETED: 0,
    EditOutcomeStatus.FAILED: 1,
    EditOutcomeStatus.REJECTED: 4,
    EditOutcomeStatus.CLARIFICATION_REQUIRED: 5,
    EditOutcomeStatus.OUT_OF_SCOPE: 6,
    EditOutcomeStatus.UNSUPPORTED_SPECIALIST: 7,
    EditOutcomeStatus.BLOCKED: 8,
}
CRITICAL_RECOVERY_EXIT_CODE = 9


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the QA-controlled HTML/CSS/SEO Flow and promote edits only on QA accept."
    )
    parser.add_argument("--target-page", required=True)
    parser.add_argument("--instruction", required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Required before any live Groq call or working-site mutation.",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    flow: AgentOrchestraFlow | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    resolved_settings = settings or get_settings()
    if not args.apply:
        print("No edit was run. Re-run with --apply to allow live Groq calls and promotion.")
        return 2
    try:
        request = EditRequest(target_page=args.target_page, instruction=args.instruction)
        report = (flow or AgentOrchestraFlow(settings=resolved_settings)).kickoff(
            inputs={"request": request.model_dump(mode="json")}
        )
        report = EditRunReport.model_validate(report)
        print_edit_run_report(report, resolved_settings)
        return EXIT_CODES[report.status]
    except PromotionRollbackError as exc:
        print("Critical: working-site recovery is required")
        print(f"error: {redact_cli_error(str(exc), resolved_settings)}")
        if exc.recovery_paths:
            print(f"recovery paths: {', '.join(exc.recovery_paths)}")
        return CRITICAL_RECOVERY_EXIT_CODE
    except (AgentOrchestraError, ValidationError, ValueError) as exc:
        print(f"Edit Flow failed: {redact_cli_error(str(exc), resolved_settings)}")
        return 1


def print_edit_run_report(report: EditRunReport, settings: Settings | None = None) -> None:
    print(f"flow outcome: {report.status.value}")
    print(f"message: {_safe(report.message, settings)}")
    if report.run_id:
        print(f"run id: {report.run_id}")
    if report.manager_result is not None:
        plan = report.manager_result.plan
        selected = ", ".join(item.value for item in plan.selected_specialists) or "none"
        print(f"manager status: {plan.status.value}")
        print(f"manager selected specialists: {selected}")
    if report.specialist_report is not None:
        print(f"specialist execution status: {report.specialist_report.status.value}")
        for result in report.specialist_report.results:
            print(f"specialist {result.specialist.value}: {result.status.value}")
            print(f"applied patches: {result.applied_patch_count}")
            print(f"rejected patches: {result.rejected_patch_count}")
            for index, patch in enumerate(result.patch_results, start=1):
                reason = patch.rejection_reason.value if patch.rejection_reason else "none"
                print(
                    f"specialist {result.specialist.value} patch {index}: "
                    f"status={patch.status.value} file={patch.file} reason={reason}"
                )
        diff = report.specialist_report.diff_report
        print(f"changed files: {', '.join(diff.changed_files) or 'none'}")
        print(f"reviewed diff added lines: {diff.total_added_lines}")
        print(f"reviewed diff removed lines: {diff.total_removed_lines}")
    if report.qa_run is not None:
        print(f"qa verdict: {report.qa_run.result.verdict.value}")
        for result in report.qa_run.result.criteria_results:
            print(
                "qa criterion: "
                f"{_safe(result.criterion, settings)} => {result.status.value}; "
                f"{_safe(result.evidence, settings)}"
            )
        print(f"qa latency ms: {report.qa_run.latency_ms:.1f}")
        usage = {
            key: value
            for key, value in report.qa_run.token_usage.model_dump(mode="json").items()
            if value is not None
        }
        print(f"qa token usage: {usage if usage else 'unavailable'}")
    else:
        print("qa run: no")
    if report.lighthouse_seo is not None:
        audit = report.lighthouse_seo
        if audit.score is not None:
            print(f"lighthouse seo score: {audit.score}")
        print(f"lighthouse failed audits: {', '.join(audit.failed_audit_ids) or 'none'}")
        if audit.error:
            print(f"lighthouse error: {_safe(audit.error, settings)}")
    if report.seo_diagnostic_report is not None:
        for finding in report.seo_diagnostic_report.findings:
            print(
                f"seo finding [{finding.severity.value}] {finding.code}: "
                f"{_safe(finding.title, settings)}"
            )
    print(f"working updated: {'yes' if report.working_updated else 'no'}")
    if report.working_restored:
        print("working restored: yes")
    print(f"staging cleaned: {'yes' if report.staging_cleaned else 'no'}")
    if report.promotion_status is not None:
        print(f"promotion status: {report.promotion_status.value}")
    if report.final_working_digest is not None:
        print(f"final content digest: {report.final_working_digest}")
    for warning in report.cleanup_warnings:
        print(f"cleanup warning: {_safe(warning, settings)}")
    print(f"total latency ms: {report.total_latency_ms:.1f}")
    if report.error:
        print(f"error: {_safe(report.error, settings)}")


def _safe(value: str, settings: Settings | None) -> str:
    if settings is None:
        return value
    clean = value
    for secret in settings.groq_api_key_values:
        clean = clean.replace(secret, "[redacted]")
    return clean


if __name__ == "__main__":
    raise SystemExit(main())
