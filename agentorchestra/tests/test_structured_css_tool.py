import json

from agentorchestra.models import SpecialistName
from agentorchestra.services.workspace import create_staged_copy, update_css_declaration
from agentorchestra.tools import PatchEvidenceRecorder, ProposePatchTool, UpdateCSSDeclarationTool
from tests.test_workspace_service import make_settings


def test_structured_css_tool_updates_existing_declaration_and_preserves_rule(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "css-structured")
    recorder = PatchEvidenceRecorder()
    tool = UpdateCSSDeclarationTool(
        handle=handle,
        allowed_files=("style.css",),
        recorder=recorder,
    )

    payload = json.loads(
        tool._run(
            selector=".button-link",
            property_name="background",
            value="#dc2626",
            summary="Make the project button red.",
        )
    )
    updated = (handle.path / "style.css").read_text(encoding="utf-8")

    assert payload["status"] == "applied"
    assert payload["file"] == "style.css"
    assert payload["specialist"] == "css"
    assert "background: #dc2626;" in updated
    assert "color: #ffffff;" in updated
    assert recorder.snapshot()[0].status.value == "applied"


def test_structured_css_tool_safely_resolves_background_color_to_color_only_shorthand(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "css-background-alias")
    tool = UpdateCSSDeclarationTool(handle=handle, allowed_files=("style.css",))

    payload = json.loads(
        tool._run(
            selector=".button-link",
            property_name="background-color",
            value="green",
            summary="Make the project button green.",
        )
    )
    updated = (handle.path / "style.css").read_text(encoding="utf-8")

    assert payload["status"] == "applied"
    assert "background: green;" in updated
    assert "color: #ffffff;" in updated


def test_raw_css_patch_cannot_add_duplicate_property_that_overrides_requested_value(tmp_path):
    settings = make_settings(tmp_path)
    for site_dir in (settings.fixture_site_dir, settings.working_site_dir):
        css = (site_dir / "style.css").read_text(encoding="utf-8")
        (site_dir / "style.css").write_text(
            css.replace(
                ".button-link {\n",
                ".button-link {\n  border-radius: 6px;\n",
            ),
            encoding="utf-8",
        )
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "css-duplicate")
    before = (handle.path / "style.css").read_text(encoding="utf-8")
    tool = ProposePatchTool(
        handle=handle,
        specialist=SpecialistName.CSS,
        allowed_files=("style.css",),
    )

    payload = json.loads(
        tool._run(
            file="style.css",
            old_text=".button-link {",
            new_text=".button-link { border-radius: 10px;",
            summary="Make the button more rounded.",
        )
    )

    assert payload["status"] == "rejected"
    assert payload["rejection_reason"] == "invalid_patch"
    assert "duplicate property 'border-radius'" in payload["message"]
    assert "Do not return completed" in payload["message"]
    assert "call update_css_declaration" in payload["message"]
    assert (handle.path / "style.css").read_text(encoding="utf-8") == before


def test_structured_css_tool_does_not_replace_image_background_via_color_alias(tmp_path):
    settings = make_settings(tmp_path)
    complex_css = (
        (settings.working_site_dir / "style.css")
        .read_text(encoding="utf-8")
        .replace("background: var(--accent);", "background: linear-gradient(red, blue);")
    )
    for site_dir in (settings.fixture_site_dir, settings.working_site_dir):
        (site_dir / "style.css").write_text(complex_css, encoding="utf-8")
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "css-complex-background")
    tool = UpdateCSSDeclarationTool(handle=handle, allowed_files=("style.css",))

    payload = json.loads(
        tool._run(
            selector=".button-link",
            property_name="background-color",
            value="green",
            summary="Do not erase the gradient.",
        )
    )

    assert payload["rejection_reason"] == "target_not_found"
    assert "linear-gradient(red, blue)" in (handle.path / "style.css").read_text(
        encoding="utf-8"
    )


def test_structured_css_tool_resolves_one_unique_shortened_class_selector(tmp_path):
    settings = make_settings(tmp_path)
    for site_dir in (settings.fixture_site_dir, settings.working_site_dir):
        css = (site_dir / "style.css").read_text(encoding="utf-8")
        (site_dir / "style.css").write_text(
            css + ".hero-section {\n  min-height: 520px;\n}\n",
            encoding="utf-8",
        )
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "css-selector-alias")
    tool = UpdateCSSDeclarationTool(handle=handle, allowed_files=("style.css",))

    payload = json.loads(
        tool._run(
            selector=".hero",
            property_name="min-height",
            value="420px",
            summary="Make the hero section shorter.",
        )
    )

    assert payload["status"] == "applied"
    assert ".hero-section {\n  min-height: 420px;" in (
        handle.path / "style.css"
    ).read_text(encoding="utf-8")


def test_structured_css_tool_resolves_height_to_one_existing_height_constraint(tmp_path):
    settings = make_settings(tmp_path)
    for site_dir in (settings.fixture_site_dir, settings.working_site_dir):
        css = (site_dir / "style.css").read_text(encoding="utf-8")
        (site_dir / "style.css").write_text(
            css + ".hero-section {\n  min-height: 520px;\n}\n",
            encoding="utf-8",
        )
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "css-height-alias")
    tool = UpdateCSSDeclarationTool(handle=handle, allowed_files=("style.css",))

    payload = json.loads(
        tool._run(
            selector=".hero",
            property_name="height",
            value="420px",
            summary="Make the hero section shorter.",
        )
    )

    assert payload["status"] == "applied"
    assert "min-height: 420px;" in (handle.path / "style.css").read_text(encoding="utf-8")


def test_structured_css_tool_does_not_guess_between_selector_extensions(tmp_path):
    settings = make_settings(tmp_path)
    for site_dir in (settings.fixture_site_dir, settings.working_site_dir):
        css = (site_dir / "style.css").read_text(encoding="utf-8")
        (site_dir / "style.css").write_text(
            css
            + ".hero-section {\n  min-height: 520px;\n}\n"
            + ".hero-banner {\n  min-height: 300px;\n}\n",
            encoding="utf-8",
        )
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "css-selector-ambiguous")
    tool = UpdateCSSDeclarationTool(handle=handle, allowed_files=("style.css",))

    payload = json.loads(
        tool._run(
            selector=".hero",
            property_name="min-height",
            value="420px",
            summary="Do not guess the hero selector.",
        )
    )

    assert payload["rejection_reason"] == "target_not_found"


def test_structured_css_tool_reports_missing_selector_and_property_without_writing(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "css-missing")
    before = (handle.path / "style.css").read_bytes()
    tool = UpdateCSSDeclarationTool(handle=handle, allowed_files=("style.css",))

    missing_selector = json.loads(
        tool._run(
            selector=".does-not-exist",
            property_name="color",
            value="red",
            summary="Missing selector.",
        )
    )
    missing_property = json.loads(
        tool._run(
            selector=".button-link",
            property_name="outline-color",
            value="red",
            summary="Missing property.",
        )
    )

    assert missing_selector["rejection_reason"] == "target_not_found"
    assert "selector" in missing_selector["message"]
    assert missing_property["rejection_reason"] == "target_not_found"
    assert "property" in missing_property["message"]
    assert (handle.path / "style.css").read_bytes() == before


def test_structured_css_tool_rejects_noop_and_unsafe_or_expansive_values(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "css-rejections")
    tool = UpdateCSSDeclarationTool(handle=handle, allowed_files=("style.css",))

    noop = json.loads(
        tool._run(
            selector=".button-link",
            property_name="color",
            value="#ffffff",
            summary="Keep white text.",
        )
    )
    extra_declaration = json.loads(
        tool._run(
            selector=".button-link",
            property_name="background",
            value="red; position: fixed",
            summary="Try multiple declarations.",
        )
    )
    external_url = json.loads(
        tool._run(
            selector=".button-link",
            property_name="background",
            value="url(https://example.com/tracker.png)",
            summary="Try an external reference.",
        )
    )

    assert noop["rejection_reason"] == "no_op"
    assert extra_declaration["rejection_reason"] == "invalid_patch"
    assert external_url["rejection_reason"] == "ownership_violation"


def test_structured_css_service_requires_css_identity_and_tool_hides_trusted_context(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "css-schema")
    tool = UpdateCSSDeclarationTool(handle=handle, allowed_files=("style.css",))

    properties = set(tool.args_schema.model_json_schema()["properties"])
    serialized = tool.model_dump(mode="json")

    assert properties == {"selector", "property_name", "value", "summary"}
    for hidden in {"handle", "specialist", "allowed_files", "recorder"}:
        assert hidden not in serialized

    try:
        update_css_declaration(
            handle,
            specialist=SpecialistName.HTML,
            selector=".button-link",
            property_name="background",
            value="red",
            summary="Wrong specialist.",
        )
    except Exception as exc:
        assert "CSS specialist" in str(exc)
    else:
        raise AssertionError("HTML identity must not use the structured CSS service.")
