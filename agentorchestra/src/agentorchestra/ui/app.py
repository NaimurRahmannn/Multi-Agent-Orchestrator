from __future__ import annotations

import streamlit as st
from pydantic import ValidationError

from agentorchestra.config import Settings, get_settings
from agentorchestra.exceptions import AgentOrchestraError, PromotionRollbackError
from agentorchestra.flow import AgentOrchestraFlow, build_production_flow_dependencies
from agentorchestra.models import EditRequest
from agentorchestra.path_safety import redact_absolute_path_text, redact_secret_like_text
from agentorchestra.pipeline_models import EditRunReport
from agentorchestra.services.promotion import reset_working_from_fixture
from agentorchestra.services.ui_support import (
    RuntimeReadiness,
    default_target_page,
    check_runtime_readiness,
    list_supported_target_pages,
)
from agentorchestra.ui.components import render_report
from agentorchestra.ui.presenters import sanitized_report


def create_production_flow(settings: Settings) -> AgentOrchestraFlow:
    return AgentOrchestraFlow(
        dependencies=build_production_flow_dependencies(settings=settings)
    )


def main(*, settings: Settings | None = None) -> None:
    st.set_page_config(page_title="AgentOrchestra — Webpage Editor", layout="wide")
    resolved = settings or get_settings()
    st.title("AgentOrchestra — Webpage Editor")
    _initialize_session()
    _render_readiness(resolved)

    try:
        pages = list_supported_target_pages(resolved)
    except AgentOrchestraError:
        pages = ()
    if not pages:
        st.error("The working sample site is unavailable or invalid.")
        return

    target_page = st.selectbox(
        "Target page",
        options=pages,
        index=pages.index(default_target_page(pages)),
    )
    instruction = st.text_area("Edit instruction", height=120)
    confirmed = st.checkbox(
        "I understand Groq, Lighthouse, and Playwright may run, and accepted edits update sites/working."
    )
    if st.button("Run edit", disabled=st.session_state.run_in_progress):
        if not confirmed:
            st.error("Confirm the execution notice before running an edit.")
        else:
            _run_edit(resolved, target_page, instruction)

    st.divider()
    reset_confirmed = st.checkbox("I confirm resetting sites/working from the fixture.")
    if st.button("Reset demo site", disabled=st.session_state.run_in_progress):
        if not reset_confirmed:
            st.error("Confirm the reset before restoring the demo site.")
        else:
            _reset_site(resolved)

    report_payload = st.session_state.get("last_report")
    if report_payload is not None:
        render_report(EditRunReport.model_validate(report_payload), resolved)


def _initialize_session() -> None:
    st.session_state.setdefault("run_in_progress", False)
    st.session_state.setdefault("last_report", None)
    st.session_state.setdefault("readiness", None)


def _run_edit(settings: Settings, target_page: str, instruction: str) -> None:
    st.session_state.run_in_progress = True
    try:
        request = EditRequest(target_page=target_page, instruction=instruction)
        report = create_production_flow(settings).kickoff(
            inputs={"request": request.model_dump(mode="json")}
        )
        safe = sanitized_report(EditRunReport.model_validate(report), settings)
        st.session_state.last_report = safe.model_dump(mode="json")
        st.rerun()
    except PromotionRollbackError as exc:
        st.error(f"Critical recovery required: {_safe_error(exc, settings)}")
    except (AgentOrchestraError, ValidationError, ValueError) as exc:
        st.error(f"Run failed safely: {_safe_error(exc, settings)}")
    finally:
        st.session_state.run_in_progress = False


def _reset_site(settings: Settings) -> None:
    st.session_state.run_in_progress = True
    try:
        result = reset_working_from_fixture(settings=settings)
        st.session_state.last_report = None
        if result.warnings:
            st.warning("Demo site reset successfully with a cleanup warning.")
        else:
            st.success("Demo site reset successfully.")
    except PromotionRollbackError as exc:
        st.error(f"Critical recovery required: {_safe_error(exc, settings)}")
    except AgentOrchestraError as exc:
        st.error(f"Reset failed safely: {_safe_error(exc, settings)}")
    finally:
        st.session_state.run_in_progress = False


def _render_readiness(settings: Settings) -> None:
    with st.sidebar.expander("Runtime readiness", expanded=False):
        if st.button("Check runtime dependencies"):
            st.session_state.readiness = check_runtime_readiness(
                settings,
                check_chromium=True,
            ).model_dump(mode="json")
        readiness_payload = st.session_state.readiness
        readiness = (
            RuntimeReadiness.model_validate(readiness_payload)
            if readiness_payload is not None
            else check_runtime_readiness(settings)
        )
        for label, value in readiness.model_dump(mode="json").items():
            display = "not checked" if value is None else "available" if value else "unavailable"
            st.write(f"{label.replace('_', ' ').title()}: {display}")


def _safe_error(exc: Exception, settings: Settings) -> str:
    clean = str(exc).replace("\n", " ")
    clean = clean.replace(str(settings.project_root), "[project]")
    for secret in settings.groq_api_key_values:
        clean = clean.replace(secret, "[redacted]")
    clean = redact_secret_like_text(redact_absolute_path_text(clean))
    return clean[:700] or exc.__class__.__name__


if __name__ == "__main__":
    main()
