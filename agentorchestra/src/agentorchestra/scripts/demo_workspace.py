from __future__ import annotations

from contextlib import suppress

from agentorchestra.config import Settings
from agentorchestra.models import SpecialistName
from agentorchestra.services.workspace import (
    cleanup_staged_workspace,
    create_staged_copy,
    generate_diff,
    get_workspace_handle,
    propose_patch,
    read_file,
)

DEMO_RUN_ID = "workspace-demo"


def main() -> int:
    settings = Settings()
    with suppress(Exception):
        cleanup_staged_workspace(get_workspace_handle(DEMO_RUN_ID, settings=settings))

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
        print(f"created staged run: {handle.run_id}")
        print(f"read lines: {before.start_line}-{before.end_line} from {before.file}")
        print(f"patch applied: {patch.file} by {patch.specialist}")
        print(f"changed files: {', '.join(report.changed_files)}")
        print(report.unified_diff, end="")
    finally:
        cleanup_staged_workspace(handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
