from __future__ import annotations

import hashlib
import sys
from contextlib import suppress
from pathlib import Path

from agentorchestra.config import Settings
from agentorchestra.models import SpecialistName
from agentorchestra.services.workspace import (
    FileToolError,
    SiteValidationError,
    WorkspaceError,
    cleanup_staged_workspace,
    create_staged_copy,
    generate_diff,
    propose_patch,
    read_file,
)
from agentorchestra.workspace_models import PatchStatus, WorkspaceHandle

DEMO_RUN_ID = "workspace-demo"


def main() -> int:
    settings = Settings()
    try:
        return _run_demo(settings)
    except (WorkspaceError, SiteValidationError, FileToolError) as exc:
        print(f"workspace demo failed: {exc}", file=sys.stderr)
        return 1


def _run_demo(settings: Settings) -> int:
    stale_handle = WorkspaceHandle(
        run_id=DEMO_RUN_ID,
        path=settings.staging_root_dir / DEMO_RUN_ID,
        staging_root=settings.staging_root_dir,
        source_working_path=settings.working_site_dir,
    )
    with suppress(WorkspaceError):
        cleanup_staged_workspace(stale_handle)

    working_before = _tree_digest(settings.working_site_dir)
    fixture_before = _tree_digest(settings.fixture_site_dir)

    handle = create_staged_copy(settings=settings, run_id_factory=lambda: DEMO_RUN_ID)
    try:
        before = read_file(handle, file="style.css", start_line=1, end_line=80)
        patch = propose_patch(
            handle,
            specialist=SpecialistName.CSS,
            file="style.css",
            old_text="  background: var(--accent);\n",
            new_text="  background: #0b3d91;\n",
            summary="Change button background to dark blue.",
        )
        report = generate_diff(handle, settings=settings)
        if patch.status is not PatchStatus.APPLIED:
            raise FileToolError(f"Demo patch was rejected: {patch.message}")
        print(f"created staged run: {handle.run_id}")
        print(f"read lines: {before.start_line}-{before.end_line} from {before.file}")
        print(f"patch status: {patch.status.value}")
        print(f"patched file: {patch.file}")
        print(f"match count: {patch.match_count}")
        print(f"changed files: {', '.join(report.changed_files)}")
        print(f"added lines: {report.total_added_lines}")
        print(f"removed lines: {report.total_removed_lines}")
        print(report.unified_diff, end="")
    finally:
        cleanup_staged_workspace(handle)

    if _tree_digest(settings.working_site_dir) != working_before:
        raise WorkspaceError("Working site changed during the staged demo.")
    if _tree_digest(settings.fixture_site_dir) != fixture_before:
        raise WorkspaceError("Fixture site changed during the staged demo.")
    print("working unchanged: yes")
    print("fixture unchanged: yes")
    print("staging cleanup: complete")
    return 0


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
