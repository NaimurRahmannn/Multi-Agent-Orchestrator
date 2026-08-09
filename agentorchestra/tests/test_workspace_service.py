import os
import shutil
from pathlib import Path

import pytest

from agentorchestra.config import Settings
from agentorchestra.models import SpecialistName
from agentorchestra.services.workspace import (
    FileToolError,
    PatchApplicationError,
    PatchRollbackError,
    SiteValidationError,
    WorkspaceError,
    cleanup_staged_workspace,
    create_staged_copy,
    find_exact_match_positions,
    generate_diff,
    get_workspace_handle,
    propose_patch,
    read_file,
    validate_staged_site,
)
from agentorchestra.workspace_models import (
    PatchRejectionReason,
    PatchStatus,
    WorkspaceHandle,
    WorkspaceLimits,
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

    restored = type(result).model_validate_json(result.model_dump_json())

    assert restored == result
    assert result.status is PatchStatus.APPLIED
    assert result.match_count == 1
    assert result.replacements == 1
    assert result.bytes_before == len(
        (settings.working_site_dir / "style.css").read_bytes()
    )
    assert result.bytes_after == len((handle.path / "style.css").read_bytes())
    assert result.before_sha256 and result.after_sha256
    assert result.before_sha256 != result.after_sha256
    assert result.rejection_reason is None
    assert "content" not in result.model_dump()
    assert "background: #0b3d91;" in (handle.path / "style.css").read_text(encoding="utf-8")
    assert "background: #0b3d91;" not in (
        settings.working_site_dir / "style.css"
    ).read_text(encoding="utf-8")


def test_propose_patch_rejects_zero_multiple_noop_and_wrong_owner(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "patch-errors")

    no_match = propose_patch(
        handle,
        specialist=SpecialistName.CSS,
        file="style.css",
        old_text="not present",
        new_text="replacement",
        summary="No match.",
    )
    wrong_owner = propose_patch(
        handle,
        specialist=SpecialistName.HTML,
        file="style.css",
        old_text="  color: #ffffff;\n",
        new_text="  color: #f8faf7;\n",
        summary="Wrong owner.",
    )
    no_op = propose_patch(
        handle,
        specialist=SpecialistName.CSS,
        file="style.css",
        old_text="}",
        new_text="}",
        summary="No-op.",
    )
    multiple = propose_patch(
        handle,
        specialist=SpecialistName.CSS,
        file="style.css",
        old_text="}\n",
        new_text="}\n/* done */\n",
        summary="Multiple matches.",
    )

    assert no_match.rejection_reason is PatchRejectionReason.TARGET_NOT_FOUND
    assert wrong_owner.rejection_reason is PatchRejectionReason.UNAUTHORIZED_FILE
    assert no_op.rejection_reason is PatchRejectionReason.NO_OP
    assert multiple.rejection_reason is PatchRejectionReason.AMBIGUOUS_TARGET
    assert all(
        result.status is PatchStatus.REJECTED
        for result in (no_match, wrong_owner, no_op, multiple)
    )


@pytest.mark.parametrize(
    ("specialist", "file", "old_text", "new_text"),
    [
        (
            SpecialistName.HTML,
            "index.html",
            "  <title>Home</title>",
            "  <title>Rewritten by HTML</title>",
        ),
        (
            SpecialistName.HTML,
            "index.html",
            "  <h1>Home</h1>",
            '  <h1 onclick="alert(1)">Home</h1>',
        ),
        (
            SpecialistName.HTML,
            "index.html",
            "</body>",
            "  <script>alert(1)</script>\n</body>",
        ),
        (
            SpecialistName.HTML,
            "index.html",
            "  <h1>Home</h1>",
            '  <h1 style="font-size: 4rem">Home</h1>',
        ),
        (
            SpecialistName.SEO,
            "index.html",
            "  <h1>Home</h1>",
            "  <h1>SEO rewrote body copy</h1>",
        ),
        (
            SpecialistName.CSS,
            "style.css",
            ":root {",
            '@import url("https://example.com/theme.css");\n:root {',
        ),
        (
            SpecialistName.CSS,
            "style.css",
            "  background: var(--accent);",
            '  background: url("https://example.com/image.png");',
        ),
    ],
)
def test_propose_patch_rejects_cross_ownership_and_active_content(
    tmp_path,
    specialist,
    file,
    old_text,
    new_text,
):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "ownership-policy")
    before = (handle.path / file).read_bytes()

    result = propose_patch(
        handle,
        specialist=specialist,
        file=file,
        old_text=old_text,
        new_text=new_text,
        summary="Attempt a cross-ownership edit.",
    )

    assert result.status is PatchStatus.REJECTED
    assert result.rejection_reason is PatchRejectionReason.OWNERSHIP_VIOLATION
    assert result.replacements == 0
    assert (handle.path / file).read_bytes() == before


@pytest.mark.parametrize(
    ("old_text", "new_text"),
    [
        (
            "  <title>Home</title>",
            "  <title>Harbor Light Web Design</title>",
        ),
        (
            "</head>",
            '  <meta name="description" content="Small static websites.">\n</head>',
        ),
        (
            "  <h1>Home</h1>",
            "  <h2>Home</h2>",
        ),
    ],
)
def test_propose_patch_allows_supported_seo_changes(tmp_path, old_text, new_text):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "seo-policy")

    result = propose_patch(
        handle,
        specialist=SpecialistName.SEO,
        file="index.html",
        old_text=old_text,
        new_text=new_text,
        summary="Apply a supported SEO edit.",
    )

    assert result.status is PatchStatus.APPLIED
    assert result.rejection_reason is None


@pytest.mark.parametrize(
    ("file", "reason"),
    [
        ("missing.css", PatchRejectionReason.FILE_NOT_FOUND),
        ("asset.svg", PatchRejectionReason.UNSUPPORTED_EXTENSION),
        ("../style.css", PatchRejectionReason.UNSAFE_PATH),
    ],
)
def test_patch_target_failures_return_structured_rejections(tmp_path, file, reason):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "target-rejection")
    working_before = (settings.working_site_dir / "style.css").read_bytes()
    fixture_before = (settings.fixture_site_dir / "style.css").read_bytes()

    result = propose_patch(
        handle,
        specialist=SpecialistName.CSS,
        file=file,
        old_text="old",
        new_text="new",
        summary="Reject target.",
    )

    assert result.status is PatchStatus.REJECTED
    assert result.rejection_reason is reason
    assert result.replacements == 0
    assert result.after_sha256 == result.before_sha256
    assert (settings.working_site_dir / "style.css").read_bytes() == working_before
    assert (settings.fixture_site_dir / "style.css").read_bytes() == fixture_before


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
    try:
        (handle.path / "style.css").symlink_to(settings.working_site_dir / "style.css")
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(SiteValidationError):
        validate_staged_site(handle)


def test_partial_copy_failure_removes_only_partial_run(tmp_path):
    settings = make_settings(tmp_path)
    working_before = (settings.working_site_dir / "style.css").read_bytes()
    fixture_before = (settings.fixture_site_dir / "style.css").read_bytes()

    def partial_copy(source, destination, **_kwargs):
        destination.mkdir()
        shutil.copy2(source / "style.css", destination / "style.css")
        raise OSError("simulated partial copy failure")

    with pytest.raises(WorkspaceError, match="Failed to create staged workspace"):
        create_staged_copy(
            settings=settings,
            run_id_factory=lambda: "partial",
            copy_function=partial_copy,
        )

    assert settings.staging_root_dir.is_dir()
    assert not (settings.staging_root_dir / "partial").exists()
    assert (settings.working_site_dir / "style.css").read_bytes() == working_before
    assert (settings.fixture_site_dir / "style.css").read_bytes() == fixture_before


@pytest.mark.parametrize("target_kind", ["working", "fixture", "outside"])
def test_read_and_patch_reject_staged_file_symlinks_before_following(tmp_path, target_kind):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: f"link-{target_kind}")
    outside = tmp_path / "outside.css"
    outside.write_text("outside", encoding="utf-8")
    targets = {
        "working": settings.working_site_dir / "style.css",
        "fixture": settings.fixture_site_dir / "style.css",
        "outside": outside,
    }
    target = targets[target_kind]
    target_before = target.read_bytes()
    (handle.path / "style.css").unlink()
    try:
        (handle.path / "style.css").symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises((SiteValidationError, WorkspaceError, FileToolError)):
        read_file(handle, file="style.css")
    with pytest.raises((SiteValidationError, WorkspaceError, FileToolError)):
        propose_patch(
            handle,
            specialist=SpecialistName.CSS,
            file="style.css",
            old_text="outside",
            new_text="changed",
            summary="Reject link.",
        )

    assert target.read_bytes() == target_before


def test_symlinked_workspace_and_staging_parent_are_rejected(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "workspace-link")
    shutil.rmtree(handle.path)
    try:
        handle.path.symlink_to(settings.working_site_dir, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(WorkspaceError, match="must not be a symlink"):
        read_file(handle, file="style.css")

    other_settings = make_settings(tmp_path / "other")
    staging_target = other_settings.project_root / "staging-target"
    staging_target.mkdir()
    try:
        other_settings.staging_root_dir.symlink_to(staging_target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    with pytest.raises(WorkspaceError, match="Staging root must not be a symlink"):
        create_staged_copy(settings=other_settings, run_id_factory=lambda: "bad-parent")


def test_assets_are_immutable_and_file_sets_must_match_working(tmp_path):
    settings = make_settings(tmp_path)
    (settings.fixture_site_dir / "assets" / "mark.png").write_bytes(b"\x89PNG\r\nfixture")
    (settings.working_site_dir / "assets" / "mark.png").write_bytes(b"\x89PNG\r\nfixture")

    unchanged = create_staged_copy(settings=settings, run_id_factory=lambda: "asset-ok")
    validate_staged_site(unchanged)

    changed_svg = create_staged_copy(settings=settings, run_id_factory=lambda: "asset-svg")
    (changed_svg.path / "assets" / "studio-mark.svg").write_bytes(b"<svg>changed</svg>")
    with pytest.raises(SiteValidationError, match="byte-for-byte"):
        validate_staged_site(changed_svg)
    with pytest.raises(SiteValidationError):
        generate_diff(changed_svg, settings=settings)

    changed_png = create_staged_copy(settings=settings, run_id_factory=lambda: "asset-png")
    (changed_png.path / "assets" / "mark.png").write_bytes(b"changed")
    with pytest.raises(SiteValidationError, match="byte-for-byte"):
        validate_staged_site(changed_png)

    added = create_staged_copy(settings=settings, run_id_factory=lambda: "asset-added")
    (added.path / "assets" / "extra.png").write_bytes(b"new")
    with pytest.raises(SiteValidationError, match="file set"):
        validate_staged_site(added)

    removed = create_staged_copy(settings=settings, run_id_factory=lambda: "asset-removed")
    (removed.path / "assets" / "mark.png").unlink()
    with pytest.raises(SiteValidationError, match="file set"):
        validate_staged_site(removed)

    renamed = create_staged_copy(settings=settings, run_id_factory=lambda: "asset-renamed")
    (renamed.path / "assets" / "mark.png").rename(renamed.path / "assets" / "renamed.png")
    with pytest.raises(SiteValidationError, match="file set"):
        validate_staged_site(renamed)

    editable = create_staged_copy(settings=settings, run_id_factory=lambda: "editable-ok")
    (editable.path / "index.html").write_text("<!doctype html>\nchanged\n", encoding="utf-8")
    (editable.path / "style.css").write_text("body { color: red; }\n", encoding="utf-8")
    validate_staged_site(editable)


def test_overlapping_exact_matches_are_ambiguous_and_do_not_write(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "overlap")
    target = handle.path / "style.css"
    target.write_text("aaa", encoding="utf-8")

    assert find_exact_match_positions("aaa", "aa") == [0, 1]
    result = propose_patch(
        handle,
        specialist=SpecialistName.CSS,
        file="style.css",
        old_text="aa",
        new_text="bb",
        summary="Ambiguous overlap.",
    )

    assert result.status is PatchStatus.REJECTED
    assert result.rejection_reason is PatchRejectionReason.AMBIGUOUS_TARGET
    assert result.match_count == 2
    assert target.read_text(encoding="utf-8") == "aaa"
    assert find_exact_match_positions("abc", "bc") == [1]


def test_file_and_patch_byte_limits_are_enforced_with_small_test_limits(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "limits")
    target = handle.path / "style.css"
    target_size = target.stat().st_size
    under = WorkspaceLimits(max_file_bytes=target_size)
    over = WorkspaceLimits(max_file_bytes=target_size - 1)

    assert read_file(handle, file="style.css", limits=under).file == "style.css"
    with pytest.raises(FileToolError, match="configured byte limit"):
        read_file(handle, file="style.css", limits=over)

    file_rejection = propose_patch(
        handle,
        specialist=SpecialistName.CSS,
        file="style.css",
        old_text="  --accent: #0f766e;",
        new_text="  --brand: #0f766e;",
        summary="File too large.",
        limits=over,
    )
    patch_rejection = propose_patch(
        handle,
        specialist=SpecialistName.CSS,
        file="style.css",
        old_text="  --accent: #0f766e;",
        new_text="  --brand: #0f766e;",
        summary="Patch too large.",
        limits=WorkspaceLimits(max_old_text_bytes=2, max_new_text_bytes=2),
    )

    assert file_rejection.rejection_reason is PatchRejectionReason.FILE_TOO_LARGE
    assert patch_rejection.rejection_reason is PatchRejectionReason.PATCH_TOO_LARGE
    assert target.stat().st_size == target_size


def test_invalid_utf8_read_and_patch_are_focused_and_leave_bytes_unchanged(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "encoding")
    target = handle.path / "style.css"
    invalid_bytes = b"body {\xff}\n"
    target.write_bytes(invalid_bytes)

    with pytest.raises(FileToolError) as read_error:
        read_file(handle, file="style.css")
    result = propose_patch(
        handle,
        specialist=SpecialistName.CSS,
        file="style.css",
        old_text="body",
        new_text="main",
        summary="Reject invalid UTF-8.",
    )

    assert "style.css" in str(read_error.value)
    assert str(tmp_path) not in str(read_error.value)
    assert result.rejection_reason is PatchRejectionReason.INVALID_ENCODING
    assert target.read_bytes() == invalid_bytes
    assert not list(handle.path.glob("*.agentorchestra-*.tmp"))


def test_atomic_patch_uses_replace_and_leaves_no_temporary_file(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "atomic")
    calls = []

    def recording_replace(source, target):
        calls.append((Path(source), Path(target)))
        os.replace(source, target)

    result = propose_patch(
        handle,
        specialist=SpecialistName.CSS,
        file="style.css",
        old_text="  --accent: #0f766e;",
        new_text="  --brand: #0f766e;",
        summary="Atomic patch.",
        replace_function=recording_replace,
    )

    assert result.status is PatchStatus.APPLIED
    assert len(calls) == 1
    assert calls[0][0].parent == calls[0][1].parent == handle.path
    assert not list(handle.path.glob("*.agentorchestra-*.tmp"))


def test_atomic_write_and_replace_failures_leave_target_unchanged(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "atomic-fail")
    target = handle.path / "style.css"
    original = target.read_bytes()

    def fail_fsync(_descriptor):
        raise OSError("simulated write failure")

    monkeypatch.setattr("agentorchestra.services.workspace.os.fsync", fail_fsync)
    with pytest.raises(PatchApplicationError, match="Atomic replacement failed"):
        propose_patch(
            handle,
            specialist=SpecialistName.CSS,
            file="style.css",
            old_text="  --accent: #0f766e;",
            new_text="  --brand: #0f766e;",
            summary="Write failure.",
        )
    assert target.read_bytes() == original
    assert not list(handle.path.glob("*.agentorchestra-*.tmp"))

    monkeypatch.undo()

    def fail_replace(_source, _target):
        raise OSError("simulated replace failure")

    with pytest.raises(PatchApplicationError, match="Atomic replacement failed"):
        propose_patch(
            handle,
            specialist=SpecialistName.CSS,
            file="style.css",
            old_text="  --accent: #0f766e;",
            new_text="  --brand: #0f766e;",
            summary="Replace failure.",
            replace_function=fail_replace,
        )
    assert target.read_bytes() == original
    assert not list(handle.path.glob("*.agentorchestra-*.tmp"))


def test_atomic_verification_failure_restores_original_bytes(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "verify-rollback")
    target = handle.path / "style.css"
    original = target.read_bytes()
    replace_calls = 0

    def corrupt_first_replacement(source, destination):
        nonlocal replace_calls
        replace_calls += 1
        os.replace(source, destination)
        if replace_calls == 1:
            Path(destination).write_bytes(b"corrupted after replacement")

    with pytest.raises(PatchApplicationError, match="original staged bytes were restored"):
        propose_patch(
            handle,
            specialist=SpecialistName.CSS,
            file="style.css",
            old_text="  --accent: #0f766e;",
            new_text="  --brand: #0f766e;",
            summary="Verify replacement.",
            replace_function=corrupt_first_replacement,
        )

    assert replace_calls == 2
    assert target.read_bytes() == original
    assert not list(handle.path.glob("*.agentorchestra-*.tmp"))


def test_post_write_validation_failure_rolls_back_original_bytes(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "rollback")
    target = handle.path / "style.css"
    original = target.read_bytes()
    validation_calls = 0

    def fail_once_after_write(workspace):
        nonlocal validation_calls
        validation_calls += 1
        if validation_calls == 2:
            raise SiteValidationError("simulated post-write failure")
        validate_staged_site(workspace)

    with pytest.raises(PatchApplicationError, match="original staged bytes were restored"):
        propose_patch(
            handle,
            specialist=SpecialistName.CSS,
            file="style.css",
            old_text="  --accent: #0f766e;",
            new_text="  --brand: #0f766e;",
            summary="Rollback patch.",
            validation_function=fail_once_after_write,
        )

    assert validation_calls == 3
    assert target.read_bytes() == original
    assert not list(handle.path.glob("*.agentorchestra-*.tmp"))


def test_failed_rollback_raises_high_signal_safety_error(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "rollback-fail")
    replace_calls = 0
    validation_calls = 0

    def fail_rollback_replace(source, target):
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 2:
            raise OSError("simulated rollback failure")
        os.replace(source, target)

    def fail_post_write(workspace):
        nonlocal validation_calls
        validation_calls += 1
        if validation_calls == 2:
            raise SiteValidationError("simulated post-write failure")
        validate_staged_site(workspace)

    with pytest.raises(PatchRollbackError, match="may require cleanup"):
        propose_patch(
            handle,
            specialist=SpecialistName.CSS,
            file="style.css",
            old_text="  --accent: #0f766e;",
            new_text="  --brand: #0f766e;",
            summary="Failed rollback.",
            replace_function=fail_rollback_replace,
            validation_function=fail_post_write,
        )
    assert not list(handle.path.glob("*.agentorchestra-*.tmp"))
    cleanup_staged_workspace(handle)


@pytest.mark.parametrize("corruption", ["extra", "missing", "asset", "unsupported"])
def test_cleanup_removes_invalid_run_only_and_is_idempotent(tmp_path, corruption):
    settings = make_settings(tmp_path)
    target = create_staged_copy(settings=settings, run_id_factory=lambda: f"cleanup-{corruption}")
    other = create_staged_copy(settings=settings, run_id_factory=lambda: "cleanup-other")
    if corruption == "extra":
        (target.path / "extra.html").write_text("extra", encoding="utf-8")
    elif corruption == "missing":
        (target.path / "index.html").unlink()
    elif corruption == "asset":
        (target.path / "assets" / "studio-mark.svg").write_text("changed", encoding="utf-8")
    else:
        (target.path / "script.js").write_text("bad", encoding="utf-8")

    cleanup_staged_workspace(target)
    cleanup_staged_workspace(target)

    assert not target.path.exists()
    assert other.path.exists()
    assert settings.staging_root_dir.exists()
    assert settings.working_site_dir.exists()
    assert settings.fixture_site_dir.exists()


@pytest.mark.parametrize("protected", ["outside", "working", "fixture", "staging"])
def test_cleanup_rejects_forged_protected_or_outside_workspace(tmp_path, protected):
    settings = make_settings(tmp_path)
    settings.staging_root_dir.mkdir(parents=True)
    outside = tmp_path / "outside-run"
    outside.mkdir()
    paths = {
        "outside": outside,
        "working": settings.working_site_dir,
        "fixture": settings.fixture_site_dir,
        "staging": settings.staging_root_dir,
    }
    protected_path = paths[protected]
    forged = WorkspaceHandle.model_construct(
        run_id=protected_path.name,
        path=protected_path,
        staging_root=settings.staging_root_dir,
        source_working_path=settings.working_site_dir,
    )

    with pytest.raises(WorkspaceError):
        cleanup_staged_workspace(forged)
    assert protected_path.exists()


def test_diff_report_empty_json_round_trip_and_size_limit(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "empty-diff")

    report = generate_diff(handle, settings=settings)
    restored = type(report).model_validate_json(report.model_dump_json())

    assert restored == report
    assert report.is_empty
    assert report.changed_files == []
    assert report.files == []
    assert report.combined_diff == report.unified_diff == ""
    assert report.total_added_lines == report.total_removed_lines == 0

    (handle.path / "index.html").write_text("<!doctype html>\nchanged\n", encoding="utf-8")
    with pytest.raises(SiteValidationError, match="Combined diff exceeds"):
        generate_diff(
            handle,
            settings=settings,
            limits=WorkspaceLimits(max_combined_diff_bytes=10),
        )


def test_rich_diff_is_sorted_deterministic_and_counts_content_lines(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "rich-diff")
    about = handle.path / "about.html"
    index = handle.path / "index.html"
    about.write_text("<!doctype html>\n+literal\n", encoding="utf-8")
    index.write_text("<!doctype html>\n-literal", encoding="utf-8")

    first = generate_diff(handle, settings=settings)
    second = generate_diff(handle, settings=settings)

    assert first == second
    assert first.changed_files == ["about.html", "index.html"]
    assert [record.file for record in first.files] == first.changed_files
    assert first.combined_diff == "".join(record.unified_diff for record in first.files)
    assert first.total_added_lines == sum(record.added_lines for record in first.files)
    assert first.total_removed_lines == sum(record.removed_lines for record in first.files)
    assert first.total_added_lines == 2
    assert first.total_removed_lines == 11
    assert "+ +literal" not in first.combined_diff
    assert "++literal" in first.combined_diff
    assert "+-literal" in first.combined_diff
    assert str(tmp_path) not in first.combined_diff
    assert "--- working/about.html" in first.combined_diff
    assert "+++ staging/index.html" in first.combined_diff
    assert first.combined_diff.endswith("\n")
