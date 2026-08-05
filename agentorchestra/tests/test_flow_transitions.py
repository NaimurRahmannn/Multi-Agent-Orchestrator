from crewai.flow import Flow

from agentorchestra.flow import AgentOrchestraFlow, AgentOrchestraFlowState


def test_production_flow_declares_real_crewai_transition_graph():
    assert issubclass(AgentOrchestraFlow, Flow)
    definition = AgentOrchestraFlow.flow_definition()
    methods = definition.methods

    assert definition.state.ref.endswith(":AgentOrchestraFlowState")
    assert methods["plan_request"].start is True
    assert methods["route_manager_plan"].router is True
    assert methods["route_manager_plan"].listen == "plan_request"
    assert set(methods["route_manager_plan"].emit or []) == {
        "clarification",
        "out_of_scope",
        "executable",
        "failed",
    }
    assert methods["create_workspace"].listen == "executable"
    assert methods["execute_specialists"].listen == "workspace_ready"
    assert methods["route_specialist_result"].router is True
    assert methods["run_seo_verification"].listen == "verification_ready"
    assert methods["route_seo_verification"].router is True
    assert methods["validate_and_build_qa_evidence"].listen == "evidence_ready"
    assert methods["execute_qa"].listen == "qa_ready"
    assert methods["route_qa_verdict"].router is True
    assert methods["promote_and_finalize"].listen == "accepted"
    assert methods["finalize_rejected"].listen == "rejected"
    assert methods["finalize_seo_diagnostic"].listen == "diagnostic_ready"


def test_flow_state_is_typed_and_excludes_dependency_objects():
    fields = AgentOrchestraFlowState.model_fields

    assert "request" in fields
    assert "manager_result" in fields
    assert "workspace_run_id" in fields
    assert "qa_evidence_digest" in fields
    assert "lighthouse_seo" in fields
    assert "seo_diagnostic_report" in fields
    assert "promotion_result" in fields
    assert "dependencies" not in fields
    assert "api_key" not in fields
