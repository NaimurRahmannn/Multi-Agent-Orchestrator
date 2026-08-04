from pathlib import Path

import pytest

from agentorchestra.config import Settings
from agentorchestra.models import SpecialistName
from agentorchestra.services.workspace import (
    FileToolError,
    SiteValidationError,
    WorkspaceError,
    cleanup_staged_workspace,
    create_staged_copy,
    generate_diff,
    get_workspace_handle,
    propose_patch,
    read_file,
    validate_staged_site,
)


def make_settings(tmp_path) -> Settings:
    root = tmp_path / "project"
    write_site(root / "sites" / "fixture")
    write_site(root / "sites" / "working")
    return Settings(project_root=root)


def write_site(root: Path) -> None:
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(
        "\n".join(
            [
                "<!doctype html>",
                '<html lang="en">',
                "<head>",
                "  <title>Home</title>",
                '  <link rel="stylesheet" href="style.css">',
                "</head>",
                "<body>",
                "  <h1>Home</h1>",
                '  <a class="button-link" href="contact.html">Start</a>',
                "</body>",
                "</html>",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (root / "about.html").write_text("<!doctype html>\n<title>About</title>\n", encoding="utf-8")
    (root / "contact.html").write_text("<!doctype html>\n<title>Contact</title>\n", encoding="utf-8")
    (root / "style.css").write_text(
        "\n".join(
            [
                ":root {",
                "  --accent: #0f766e;",
                "}",
                "",
                ".button-link {",
                "  background: var(--accent);",
                "  color: #ffffff;",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (root / "assets" / "studio-mark.svg").write_text("<svg></svg>\n", encoding="utf-8")


def test_create_lookup_and_cleanup_staged_copy(tmp_path):
    settings = make_settings(tmp_path)

    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "run123")
    looked_up = get_workspace_handle("run123", settings=settings)

    assert looked_up.path == handle.path
    assert (handle.path / "index.html").read_text(encoding="utf-8") == (
        settings.working_site_dir / "index.html"
    ).read_text(encoding="utf-8")

    cleanup_staged_workspace(handle)

    assert not handle.path.exists()
    assert (settings.working_site_dir / "index.html").exists()
    assert (settings.fixture_site_dir / "index.html").exists()


def test_create_staged_copy_rejects_duplicate_run_id(tmp_path):
    settings = make_settings(tmp_path)
    create_staged_copy(settings=settings, run_id_factory=lambda: "fixed")

    with pytest.raises(WorkspaceError):
        create_staged_copy(settings=settings, run_id_factory=lambda: "fixed")


def test_read_file_returns_bounded_exact_lines(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "read")

    result = read_file(handle, file="style.css", start_line=5, end_line=7)

    assert result.content == ".button-link {\n  background: var(--accent);\n  color: #ffffff;\n"
    assert result.start_line == 5
    assert result.end_line == 7
    assert result.total_lines == 8


def test_read_file_rejects_unsafe_paths_and_unbounded_ranges(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "unsafe-read")

    with pytest.raises(FileToolError):
        read_file(handle, file="../style.css")
    with pytest.raises(FileToolError):
        read_file(handle, file="assets/studio-mark.svg")
    with pytest.raises(FileToolError):
        read_file(handle, file="style.css", start_line=1, end_line=121)


def test_propose_patch_applies_exact_unique_match_only_to_staging(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "patch")

    result = propose_patch(
        handle,
        specialist=SpecialistName.CSS,
        file="style.css",
        old_text="  background: var(--accent);\n",
        new_text="  background: #0b3d91;\n",
        summary="Update button background.",
    )

    assert result.replacements == 1
    assert "background: #0b3d91;" in (handle.path / "style.css").read_text(encoding="utf-8")
    assert "background: #0b3d91;" not in (
        settings.working_site_dir / "style.css"
    ).read_text(encoding="utf-8")


def test_propose_patch_rejects_zero_multiple_noop_and_wrong_owner(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "patch-errors")

    with pytest.raises(FileToolError):
        propose_patch(
            handle,
            specialist=SpecialistName.CSS,
            file="style.css",
            old_text="not present",
            new_text="replacement",
            summary="No match.",
        )
    with pytest.raises(FileToolError):
        propose_patch(
            handle,
            specialist=SpecialistName.HTML,
            file="style.css",
            old_text="  color: #ffffff;\n",
            new_text="  color: #f8faf7;\n",
            summary="Wrong owner.",
        )
    with pytest.raises(FileToolError):
        propose_patch(
            handle,
            specialist=SpecialistName.CSS,
            file="style.css",
            old_text="}",
            new_text="}",
            summary="No-op.",
        )
    with pytest.raises(FileToolError):
        propose_patch(
            handle,
            specialist=SpecialistName.CSS,
            file="style.css",
            old_text="}\n",
            new_text="}\n/* done */\n",
            summary="Multiple matches.",
        )


def test_generate_diff_is_deterministic_and_uses_working_as_baseline(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "diff")
    propose_patch(
        handle,
        specialist=SpecialistName.HTML,
        file="index.html",
        old_text="  <h1>Home</h1>\n",
        new_text="  <h1>Updated home</h1>\n",
        summary="Update heading.",
    )

    first = generate_diff(handle, settings=settings)
    second = generate_diff(handle, settings=settings)

    assert first == second
    assert first.changed_files == ["index.html"]
    assert "--- working/index.html" in first.unified_diff
    assert "+++ staging/index.html" in first.unified_diff
    assert "+  <h1>Updated home</h1>" in first.unified_diff


def test_validate_staged_site_rejects_extra_files_and_symlinks(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "validate")
    (handle.path / "extra.html").write_text("bad", encoding="utf-8")

    with pytest.raises(SiteValidationError):
        validate_staged_site(handle)

    (handle.path / "extra.html").unlink()
    (handle.path / "style.css").unlink()
    (handle.path / "style.css").symlink_to(settings.working_site_dir / "style.css")

    with pytest.raises(SiteValidationError):
        validate_staged_site(handle)
