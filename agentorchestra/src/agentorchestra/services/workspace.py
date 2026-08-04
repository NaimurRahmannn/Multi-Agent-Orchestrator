from __future__ import annotations

import difflib
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path, PurePath

from pydantic import ValidationError

from agentorchestra.config import Settings, ensure_runtime_directories, get_settings
from agentorchestra.exceptions import DomainValidationError
from agentorchestra.models import PatchProposal, SpecialistName
from agentorchestra.workspace_models import (
    DiffReport,
    FileReadResult,
    PatchExecutionResult,
    WorkspaceHandle,
)

APPROVED_SITE_FILES: tuple[Path, ...] = (
    Path("about.html"),
    Path("assets/studio-mark.svg"),
    Path("contact.html"),
    Path("index.html"),
    Path("style.css"),
)
APPROVED_EDIT_EXTENSIONS = {".css", ".html"}
MAX_READ_LINES = 120
RunIdFactory = Callable[[], str]


class WorkspaceError(DomainValidationError):
    """Raised when staged workspace creation, lookup, or cleanup is unsafe."""


class SiteValidationError(DomainValidationError):
    """Raised when a site tree does not match the approved static-site structure."""


class FileToolError(DomainValidationError):
    """Raised by deterministic staged-workspace file tools."""


def create_staged_copy(
    *,
    settings: Settings | None = None,
    run_id_factory: RunIdFactory | None = None,
) -> WorkspaceHandle:
    """Create a safe staged copy of the current working site."""
    resolved_settings = settings or get_settings()
    ensure_runtime_directories(resolved_settings)
    working_path = resolved_settings.working_site_dir.resolve()
    staging_root = resolved_settings.staging_root_dir.resolve()

    validate_site_structure(working_path)
    run_id = (run_id_factory or _uuid_run_id)()
    handle = WorkspaceHandle(
        run_id=run_id,
        path=staging_root / run_id,
        staging_root=staging_root,
        source_working_path=working_path,
    )
    if handle.path.exists():
        raise WorkspaceError(f"Staged workspace already exists for run_id: {run_id}")

    shutil.copytree(working_path, handle.path, symlinks=False)
    try:
        validate_staged_site(handle)
    except Exception:
        shutil.rmtree(handle.path, ignore_errors=True)
        raise
    return handle


def get_workspace_handle(run_id: str, *, settings: Settings | None = None) -> WorkspaceHandle:
    """Return a validated handle for an existing staged run."""
    resolved_settings = settings or get_settings()
    staging_root = resolved_settings.staging_root_dir.resolve()
    handle = WorkspaceHandle(run_id=run_id, path=staging_root / run_id, staging_root=staging_root)
    if not handle.path.is_dir():
        raise WorkspaceError(f"Staged workspace does not exist for run_id: {run_id}")
    validate_staged_site(handle)
    return handle


def cleanup_staged_workspace(handle: WorkspaceHandle) -> None:
    """Delete one validated staged run directory."""
    validated = _validate_handle(handle)
    if validated.path.exists():
        validate_staged_site(validated)
        shutil.rmtree(validated.path)


def validate_staged_site(handle: WorkspaceHandle) -> None:
    """Validate that a staged run contains exactly the approved sample-site structure."""
    validated = _validate_handle(handle)
    if not validated.path.is_dir():
        raise SiteValidationError("Staged workspace path does not exist.")
    validate_site_structure(validated.path)


def validate_site_structure(site_root: Path) -> None:
    """Validate a fixture, working, or staged site tree against the approved structure."""
    root = site_root.resolve()
    if not root.is_dir():
        raise SiteValidationError(f"Site root does not exist: {root}")

    seen_files: list[Path] = []
    seen_dirs: list[Path] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_symlink():
            raise SiteValidationError(f"Symlink is not allowed in site tree: {relative.as_posix()}")
        if path.is_dir():
            seen_dirs.append(relative)
        elif path.is_file():
            seen_files.append(relative)
        else:
            raise SiteValidationError(f"Unsupported filesystem entry in site tree: {relative.as_posix()}")

    expected_files = sorted(APPROVED_SITE_FILES)
    if sorted(seen_files) != expected_files:
        raise SiteValidationError("Site files do not match the approved sample-site structure.")
    if sorted(seen_dirs) != [Path("assets")]:
        raise SiteValidationError("Site directories do not match the approved sample-site structure.")


def read_file(
    handle: WorkspaceHandle,
    *,
    file: str,
    start_line: int = 1,
    end_line: int | None = None,
) -> FileReadResult:
    """Read a bounded one-based line range from an approved staged file."""
    path = _resolve_tool_file(handle, file)
    if start_line < 1:
        raise FileToolError("start_line must be at least 1.")
    requested_end_line = end_line if end_line is not None else start_line + MAX_READ_LINES - 1
    if requested_end_line < start_line:
        raise FileToolError("end_line must be greater than or equal to start_line.")
    if requested_end_line - start_line + 1 > MAX_READ_LINES:
        raise FileToolError(f"read_file is limited to {MAX_READ_LINES} lines per call.")

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    total_lines = len(lines)
    if total_lines == 0:
        raise FileToolError("Cannot read an empty file.")
    if start_line > total_lines:
        raise FileToolError("start_line is beyond the end of the file.")

    actual_end_line = min(requested_end_line, total_lines)
    content = "".join(lines[start_line - 1 : actual_end_line])
    truncated = requested_end_line < total_lines
    return FileReadResult(
        file=file,
        start_line=start_line,
        end_line=actual_end_line,
        total_lines=total_lines,
        content=content,
        truncated=truncated,
    )


def propose_patch(
    handle: WorkspaceHandle,
    *,
    specialist: SpecialistName | str,
    file: str,
    old_text: str,
    new_text: str,
    summary: str,
) -> PatchExecutionResult:
    """Apply one exact unique old_text to new_text replacement in staging."""
    try:
        proposal = PatchProposal(
            agent=SpecialistName(specialist),
            file=file,
            old_text=old_text,
            new_text=new_text,
            summary=summary,
        )
    except (ValueError, ValidationError) as exc:
        raise FileToolError(f"Invalid patch proposal: {exc}") from exc

    path = _resolve_tool_file(handle, proposal.file)
    content = path.read_text(encoding="utf-8")
    matches = content.count(proposal.old_text)
    if matches == 0:
        raise FileToolError("old_text must match exactly once; found zero matches.")
    if matches > 1:
        raise FileToolError("old_text must match exactly once; found multiple matches.")

    path.write_text(content.replace(proposal.old_text, proposal.new_text, 1), encoding="utf-8")
    validate_staged_site(handle)
    return PatchExecutionResult(
        file=proposal.file,
        specialist=proposal.agent.value,
        summary=proposal.summary,
        replacements=1,
    )


def generate_diff(handle: WorkspaceHandle, *, settings: Settings | None = None) -> DiffReport:
    """Generate deterministic unified diff text from working to this staged copy."""
    resolved_settings = settings or get_settings()
    working_root = resolved_settings.working_site_dir.resolve()
    validate_site_structure(working_root)
    validate_staged_site(handle)

    changed_files: list[str] = []
    diff_parts: list[str] = []
    for relative_path in sorted(APPROVED_SITE_FILES):
        working_file = working_root / relative_path
        staged_file = handle.path / relative_path
        working_lines = working_file.read_text(encoding="utf-8").splitlines(keepends=True)
        staged_lines = staged_file.read_text(encoding="utf-8").splitlines(keepends=True)
        if working_lines == staged_lines:
            continue
        relative_name = relative_path.as_posix()
        changed_files.append(relative_name)
        diff_parts.extend(
            difflib.unified_diff(
                working_lines,
                staged_lines,
                fromfile=f"working/{relative_name}",
                tofile=f"staging/{relative_name}",
                lineterm="\n",
            )
        )

    unified_diff = "".join(diff_parts)
    return DiffReport(
        run_id=handle.run_id,
        changed_files=changed_files,
        unified_diff=unified_diff,
    )


def _uuid_run_id() -> str:
    return uuid.uuid4().hex


def _validate_handle(handle: WorkspaceHandle) -> WorkspaceHandle:
    try:
        return WorkspaceHandle.model_validate(handle)
    except ValidationError as exc:
        raise WorkspaceError(f"Invalid workspace handle: {exc}") from exc


def _resolve_tool_file(handle: WorkspaceHandle, file: str) -> Path:
    validated = _validate_handle(handle)
    _validate_simple_edit_file(file)
    path = (validated.path / file).resolve()
    try:
        path.relative_to(validated.path)
    except ValueError as exc:
        raise FileToolError("file must stay inside the staged workspace.") from exc
    if path.is_symlink():
        raise FileToolError("file must not be a symlink.")
    if not path.is_file():
        raise FileToolError(f"file does not exist in staged workspace: {file}")
    return path


def _validate_simple_edit_file(file: str) -> None:
    if "\x00" in file:
        raise FileToolError("file path must not contain null bytes.")
    if not file or not file.strip():
        raise FileToolError("file path must not be blank.")
    if file != file.strip():
        raise FileToolError("file path must not include surrounding whitespace.")
    if "/" in file or "\\" in file:
        raise FileToolError("file path must be a simple filename.")
    path = PurePath(file)
    if path.is_absolute() or path.name != file:
        raise FileToolError("file path must be a simple relative filename.")
    if file in {".", ".."} or ".." in file.split(".") or file.startswith("."):
        raise FileToolError("file path must not contain path traversal or hidden filenames.")
    if path.suffix not in APPROVED_EDIT_EXTENSIONS:
        raise FileToolError("file path must target an approved .html or .css file.")
