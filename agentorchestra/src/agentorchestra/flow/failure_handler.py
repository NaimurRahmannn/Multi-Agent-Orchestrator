from __future__ import annotations

from typing import TYPE_CHECKING

from agentorchestra.exceptions import ExecutionEvidenceError, PromotionRollbackError
from agentorchestra.flow.state import recorder
from agentorchestra.models import SpecialistName
from agentorchestra.observability_models import TimelineEventStatus, TimelineStage
from agentorchestra.path_safety import redact_absolute_path_text

if TYPE_CHECKING:
    from agentorchestra.flow.orchestration import AgentOrchestraFlow


def cleanup_nonaccepted_workspace(flow: AgentOrchestraFlow) -> bool:
    """Discard one non-accepted staging workspace and record cleanup evidence."""
    if flow.state.workspace_run_id is None:
        return True
    token = recorder(flow).start(TimelineStage.CLEANUP)
    try:
        handle = flow._workspace_handle or flow._dependencies.workspace_lookup(
            flow.state.workspace_run_id,
            settings=flow._dependencies.settings,
        )
        flow._dependencies.workspace_cleanup(handle)
    except Exception:
        warning = f"Could not remove staged run '{flow.state.workspace_run_id}'."
        if warning not in flow.state.warnings:
            flow.state.warnings.append(warning)
        recorder(flow).finish(
            token,
            status=TimelineEventStatus.WARNING,
            message="Staged workspace cleanup could not be verified.",
        )
        return False
    cleaned = not handle.path.exists() and not handle.path.is_symlink()
    recorder(flow).finish(
        token,
        status=(TimelineEventStatus.SUCCEEDED if cleaned else TimelineEventStatus.WARNING),
        message=(
            "Discarded staged workspace cleaned."
            if cleaned
            else "Staged workspace cleanup could not be verified."
        ),
    )
    return cleaned


def record_failure(flow: AgentOrchestraFlow, exc: Exception, message: str) -> None:
    """Persist one sanitized failure unless operator recovery must escape the Flow."""
    if isinstance(exc, PromotionRollbackError):
        raise exc
    flow.state.error = safe_error(flow, exc)
    flow.state.failure_message = message


def record_missing_specialist_failure_events(flow: AgentOrchestraFlow) -> None:
    """Backfill timeline failures when specialist execution aborts before returning evidence."""
    plan = flow.state.plan
    if plan is None:
        return
    recorded = {
        event.specialist
        for event in recorder(flow).snapshot().events
        if event.specialist is not None
    }
    for specialist in plan.selected_specialists:
        if specialist not in recorded:
            recorder(flow).record(
                specialist_stage(specialist),
                specialist=specialist,
                status=TimelineEventStatus.FAILED,
                message=f"{specialist.value.upper()} specialist failed safely.",
            )


def specialist_stage(specialist: SpecialistName) -> TimelineStage:
    return {
        SpecialistName.HTML: TimelineStage.SPECIALIST_HTML,
        SpecialistName.CSS: TimelineStage.SPECIALIST_CSS,
        SpecialistName.SEO: TimelineStage.SPECIALIST_SEO,
    }[specialist]


def safe_error(flow: AgentOrchestraFlow, exc: Exception) -> str:
    """Return bounded, path-redacted, secret-redacted failure text."""
    clean = str(exc).replace("\n", " ").strip()
    clean = clean.replace(str(flow._dependencies.settings.project_root), "[project]")
    for secret in flow._dependencies.settings.groq_api_key_values:
        clean = clean.replace(secret, "[redacted]")
    clean = redact_absolute_path_text(clean)
    if isinstance(exc, ExecutionEvidenceError):
        return f"Execution evidence validation failed: {clean[:700]}"
    return clean[:700] or exc.__class__.__name__
