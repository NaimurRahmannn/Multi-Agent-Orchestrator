from __future__ import annotations

import hashlib
from pathlib import Path

from agentorchestra.config import Settings
from agentorchestra.models import ManagerRoutingPlan
from agentorchestra.specialist_models import SpecialistExecutionReport


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def print_manager_plan(plan: ManagerRoutingPlan, settings: Settings | None = None) -> None:
    print(f"manager status: {plan.status.value}")
    selected = ", ".join(item.value for item in plan.selected_specialists) or "none"
    print(f"manager selected specialists: {selected}")
    print(f"manager rationale: {_safe_text(plan.routing_rationale, settings)}")
    for assignment in plan.assignments:
        print(
            f"manager assignment [{assignment.agent.value}]: "
            f"{_safe_text(assignment.task, settings)}"
        )
    for criterion in plan.acceptance_criteria:
        print(f"acceptance criterion: {_safe_text(criterion, settings)}")


def print_execution_report(
    report: SpecialistExecutionReport, settings: Settings | None = None
) -> None:
    print(f"execution status: {report.status.value}")
    print(f"run id: {report.run_id}")
    print(f"stopped early: {'yes' if report.stopped_early else 'no'}")
    for result in report.results:
        print(f"specialist: {result.specialist.value}")
        print(f"runtime status: {result.status.value}")
        if result.completion is not None:
            print(f"completion status: {result.completion.status.value}")
            print(f"completion summary: {_safe_text(result.completion.summary, settings)}")
            if result.completion.remaining_issue:
                print(
                    f"remaining issue: {_safe_text(result.completion.remaining_issue, settings)}"
                )
        print(f"applied patches: {result.applied_patch_count}")
        print(f"rejected patches: {result.rejected_patch_count}")
        for index, patch in enumerate(result.patch_results, start=1):
            reason = patch.rejection_reason.value if patch.rejection_reason else "none"
            print(
                f"patch {index}: status={patch.status.value} file={patch.file} "
                f"reason={reason} message={_safe_text(patch.message, settings)}"
            )
        print(f"changed files: {', '.join(result.changed_files) or 'none'}")
        print(f"latency ms: {result.latency_ms:.1f}")
        usage = {
            key: value
            for key, value in result.token_usage.model_dump(mode="json").items()
            if value is not None
        }
        print(f"token usage: {usage if usage else 'unavailable'}")
        if result.error:
            print(f"error: {_safe_text(result.error, settings)}")
    print(f"diff changed files: {', '.join(report.diff_report.changed_files) or 'none'}")
    print(f"diff added lines: {report.diff_report.total_added_lines}")
    print(f"diff removed lines: {report.diff_report.total_removed_lines}")
    print("final unified diff:")
    print(_safe_text(report.diff_report.combined_diff, settings), end="")
    if not report.diff_report.combined_diff:
        print("(empty)")


def redact_cli_error(message: str, settings: Settings) -> str:
    clean = message.replace("\n", " ").strip()
    if settings.groq_api_key:
        secret = settings.groq_api_key.get_secret_value()
        if secret:
            clean = clean.replace(secret, "[redacted]")
    return clean[:900]


def _safe_text(value: str, settings: Settings | None) -> str:
    if settings is None or not settings.groq_api_key:
        return value
    secret = settings.groq_api_key.get_secret_value()
    return value.replace(secret, "[redacted]") if secret else value
