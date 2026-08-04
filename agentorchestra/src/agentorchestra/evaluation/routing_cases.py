from __future__ import annotations

from agentorchestra.models import RoutingEvidenceCase, RoutingStatus, SpecialistName

REQUIRED_ROUTING_CASES: tuple[RoutingEvidenceCase, ...] = (
    RoutingEvidenceCase(
        case_id="required_css_button_color",
        request="Change the button color to dark blue",
        expected_status=RoutingStatus.EXECUTE,
        expected_specialists=[SpecialistName.CSS],
    ),
    RoutingEvidenceCase(
        case_id="required_html_broken_div",
        request="Fix this broken <div> tag",
        expected_status=RoutingStatus.EXECUTE,
        expected_specialists=[SpecialistName.HTML],
    ),
    RoutingEvidenceCase(
        case_id="required_html_css_heading_alt",
        request="Make the heading bigger and add missing alt text",
        expected_status=RoutingStatus.EXECUTE,
        expected_specialists=[SpecialistName.HTML, SpecialistName.CSS],
    ),
    RoutingEvidenceCase(
        case_id="required_backend_unsupported",
        request="Add a contact form with backend validation",
        expected_status=RoutingStatus.OUT_OF_SCOPE,
        expected_specialists=[],
    ),
    RoutingEvidenceCase(
        case_id="required_ambiguous_make_better",
        request="Make it better",
        expected_status=RoutingStatus.CLARIFICATION_REQUIRED,
        expected_specialists=[],
    ),
    RoutingEvidenceCase(
        case_id="required_html_alt_text",
        request="Add alt text to the hero image",
        expected_status=RoutingStatus.EXECUTE,
        expected_specialists=[SpecialistName.HTML],
    ),
    RoutingEvidenceCase(
        case_id="required_seo_diagnosis",
        request="This page will not rank; what is missing?",
        expected_status=RoutingStatus.EXECUTE,
        expected_specialists=[SpecialistName.SEO],
    ),
    RoutingEvidenceCase(
        case_id="required_seo_css_meta_heading",
        request="Add a meta description and make the main heading bigger",
        expected_status=RoutingStatus.EXECUTE,
        expected_specialists=[SpecialistName.SEO, SpecialistName.CSS],
    ),
)

DIAGNOSTIC_ROUTING_CASES: tuple[RoutingEvidenceCase, ...] = (
    RoutingEvidenceCase(
        case_id="diagnostic_css_only",
        request="Make the buttons use a darker teal background and slightly larger text.",
        expected_status=RoutingStatus.EXECUTE,
        expected_specialists=[SpecialistName.CSS],
    ),
    RoutingEvidenceCase(
        case_id="diagnostic_html_only",
        request="Fix the contact form so the email label is clearly connected to the email field.",
        expected_status=RoutingStatus.EXECUTE,
        expected_specialists=[SpecialistName.HTML],
    ),
    RoutingEvidenceCase(
        case_id="diagnostic_html_css",
        request="Add a small services note to the homepage and style it so it stands apart.",
        expected_status=RoutingStatus.EXECUTE,
        expected_specialists=[SpecialistName.HTML, SpecialistName.CSS],
    ),
    RoutingEvidenceCase(
        case_id="diagnostic_backend_unsupported",
        request="Create a backend endpoint that stores contact form submissions in a database.",
        expected_status=RoutingStatus.OUT_OF_SCOPE,
        expected_specialists=[],
    ),
    RoutingEvidenceCase(
        case_id="diagnostic_ambiguous",
        request="Make the website pop more.",
        expected_status=RoutingStatus.CLARIFICATION_REQUIRED,
        expected_specialists=[],
    ),
    RoutingEvidenceCase(
        case_id="diagnostic_alt_text",
        request="Improve the alt text for the lighthouse image on the about page.",
        expected_status=RoutingStatus.EXECUTE,
        expected_specialists=[SpecialistName.HTML],
    ),
    RoutingEvidenceCase(
        case_id="diagnostic_seo_diagnosis",
        request="Diagnose the homepage for basic on-page SEO issues.",
        expected_status=RoutingStatus.EXECUTE,
        expected_specialists=[SpecialistName.SEO],
    ),
    RoutingEvidenceCase(
        case_id="diagnostic_seo_css",
        request="Improve the homepage title for SEO and make the hero call-to-action more prominent.",
        expected_status=RoutingStatus.EXECUTE,
        expected_specialists=[SpecialistName.SEO, SpecialistName.CSS],
    ),
    RoutingEvidenceCase(
        case_id="diagnostic_title_meta",
        request="Update the about page title and meta description for search results.",
        expected_status=RoutingStatus.EXECUTE,
        expected_specialists=[SpecialistName.SEO],
    ),
    RoutingEvidenceCase(
        case_id="diagnostic_clear_css",
        request="Increase the spacing between navigation links.",
        expected_status=RoutingStatus.EXECUTE,
        expected_specialists=[SpecialistName.CSS],
    ),
)

_CASE_TARGET_PAGES = {
    "diagnostic_alt_text": "about.html",
    "diagnostic_title_meta": "about.html",
}


def target_page_for_case(case: RoutingEvidenceCase) -> str:
    return _CASE_TARGET_PAGES.get(case.case_id, "index.html")
