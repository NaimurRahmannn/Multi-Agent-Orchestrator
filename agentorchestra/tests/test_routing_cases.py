from agentorchestra.evaluation.routing_cases import (
    DIAGNOSTIC_ROUTING_CASES,
    REQUIRED_ROUTING_CASES,
    target_page_for_case,
)
from agentorchestra.models import RoutingStatus, SpecialistName


def test_exactly_required_approved_cases_are_present():
    assert [case.case_id for case in REQUIRED_ROUTING_CASES] == [
        "required_css_button_color",
        "required_html_broken_div",
        "required_html_css_heading_alt",
        "required_backend_unsupported",
        "required_ambiguous_make_better",
        "required_html_alt_text",
        "required_seo_diagnosis",
        "required_seo_css_meta_heading",
    ]


def test_required_case_routes_match_manager_brief():
    expected = {
        "required_css_button_color": (RoutingStatus.EXECUTE, {SpecialistName.CSS}),
        "required_html_broken_div": (RoutingStatus.EXECUTE, {SpecialistName.HTML}),
        "required_html_css_heading_alt": (
            RoutingStatus.EXECUTE,
            {SpecialistName.HTML, SpecialistName.CSS},
        ),
        "required_backend_unsupported": (RoutingStatus.OUT_OF_SCOPE, set()),
        "required_ambiguous_make_better": (RoutingStatus.CLARIFICATION_REQUIRED, set()),
        "required_html_alt_text": (RoutingStatus.EXECUTE, {SpecialistName.HTML}),
        "required_seo_diagnosis": (RoutingStatus.EXECUTE, {SpecialistName.SEO}),
        "required_seo_css_meta_heading": (
            RoutingStatus.EXECUTE,
            {SpecialistName.SEO, SpecialistName.CSS},
        ),
    }

    for case in REQUIRED_ROUTING_CASES:
        status, specialists = expected[case.case_id]
        assert case.request
        assert case.expected_status is status
        assert set(case.expected_specialists) == specialists
        assert "qa" not in [specialist.value for specialist in case.expected_specialists]


def test_case_ids_are_unique_across_required_and_optional_cases():
    case_ids = [case.case_id for case in REQUIRED_ROUTING_CASES + DIAGNOSTIC_ROUTING_CASES]

    assert len(case_ids) == len(set(case_ids))


def test_clarification_and_out_of_scope_cases_have_no_specialists():
    for case in REQUIRED_ROUTING_CASES:
        if case.expected_status is not RoutingStatus.EXECUTE:
            assert case.expected_specialists == []


def test_optional_cases_are_clearly_separate_and_have_target_pages():
    assert all(case.case_id.startswith("diagnostic_") for case in DIAGNOSTIC_ROUTING_CASES)
    assert target_page_for_case(DIAGNOSTIC_ROUTING_CASES[5]) == "about.html"
    assert target_page_for_case(REQUIRED_ROUTING_CASES[0]) == "index.html"
