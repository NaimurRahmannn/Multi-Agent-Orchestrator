from __future__ import annotations

import difflib
import hashlib
import os
import shutil
import stat
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path, PurePath

from pydantic import ValidationError

from agentorchestra.config import Settings, ensure_runtime_directories, get_settings
from agentorchestra.exceptions import DomainValidationError
from agentorchestra.models import PatchProposal, SpecialistName
from agentorchestra.workspace_models import (
    DiffReport,
    FileDiff,
    FileReadResult,
    PatchExecutionResult,
    PatchRejectionReason,
    PatchStatus,
    WorkspaceHandle,
    WorkspaceLimits,
)

APPROVED_EDIT_EXTENSIONS = {".css", ".html"}
REQUIRED_EDITABLE_FILES = {
    Path("about.html"),
    Path("contact.html"),
    Path("index.html"),
    Path("style.css"),
}
DEFAULT_WORKSPACE_LIMITS = WorkspaceLimits()
MAX_READ_LINES = DEFAULT_WORKSPACE_LIMITS.max_read_lines

RunIdFactory = Callable[[], str]
CopyFunction = Callable[..., object]
ReplaceFunction = Callable[[str | bytes | os.PathLike[str] | os.PathLike[bytes], str | bytes | os.PathLike[str] | os.PathLike[bytes]], None]
ValidationFunction = Callable[[WorkspaceHandle], None]


class WorkspaceError(DomainValidationError):
    """Raised when staged workspace creation, lookup, or cleanup is unsafe."""


class SiteValidationError(DomainValidationError):
    """Raised when a site tree violates the fixed static-site contract."""


class FileToolError(DomainValidationError):
    """Raised by deterministic staged-workspace file tools."""


class PatchApplicationError(FileToolError):
    """Raised when an atomic write or its safety validation fails."""


class PatchRollbackError(PatchApplicationError):
    """Raised when original staged bytes cannot be restored and verified."""


class _FileSizeLimitError(FileToolError):
    pass


class _AtomicWriteError(OSError):
    def __init__(self, message: str, *, replacement_completed: bool) -> None:
        super().__init__(message)
        self.replacement_completed = replacement_completed


def create_staged_copy(
    *,
    settings: Settings | None = None,
    run_id_factory: RunIdFactory | None = None,
    copy_function: CopyFunction | None = None,
) -> WorkspaceHandle:
    """Create a validated staged copy and remove partial output on any failure."""
    resolved_settings = settings or get_settings()
    ensure_runtime_directories(resolved_settings)
    working_path = resolved_settings.working_site_dir
    staging_root = resolved_settings.staging_root_dir

    validate_site_structure(working_path)
    _validate_staging_root(staging_root)
    run_id = (run_id_factory or _uuid_run_id)()
    try:
        handle = WorkspaceHandle(
            run_id=run_id,
            path=staging_root / run_id,
            staging_root=staging_root,
            source_working_path=working_path,
        )
    except ValidationError as exc:
        raise WorkspaceError("Generated run ID or staged path is invalid.") from exc
    _validate_workspace_boundary(handle, require_exists=False)
    if handle.path.exists() or handle.path.is_symlink():
        raise WorkspaceError(f"Staged workspace already exists for run_id: {run_id}")

    copier = copy_function or shutil.copytree
    try:
        copier(working_path, handle.path, symlinks=False)
        validate_staged_site(handle)
    except Exception as exc:
        try:
            _remove_run_directory(handle)
        except Exception as cleanup_exc:
            raise WorkspaceError(
                f"Staged copy failed and partial run {run_id} could not be removed."
            ) from cleanup_exc
        if isinstance(exc, WorkspaceError | SiteValidationError):
            raise
        raise WorkspaceError(f"Failed to create staged workspace for run_id: {run_id}") from exc
    return handle


def get_workspace_handle(run_id: str, *, settings: Settings | None = None) -> WorkspaceHandle:
    """Return a validated handle for an existing staged run."""
    resolved_settings = settings or get_settings()
    staging_root = resolved_settings.staging_root_dir
    try:
        handle = WorkspaceHandle(
            run_id=run_id,
            path=staging_root / run_id,
            staging_root=staging_root,
            source_working_path=resolved_settings.working_site_dir,
        )
    except ValidationError as exc:
        raise WorkspaceError("Invalid staged workspace run ID.") from exc
    _validate_workspace_boundary(handle, require_exists=True)
    validate_staged_site(handle)
    return handle


def cleanup_staged_workspace(handle: WorkspaceHandle) -> None:
    """Idempotently delete one path-bounded run, even when its contents are invalid."""
    validated = _validate_handle(handle)
    _validate_workspace_boundary(validated, require_exists=False)
    _remove_run_directory(validated)


def validate_staged_site(handle: WorkspaceHandle) -> None:
    """Validate staged structure and enforce immutable assets against working."""
    validated = _validate_handle(handle)
    staged_root = _validate_workspace_boundary(validated, require_exists=True)
    working_root = validated.source_working_path
    if working_root is None:
        raise WorkspaceError("Workspace handle does not identify its working-site baseline.")

    staged_files, staged_dirs = _site_inventory(staged_root)
    working_files, working_dirs = _site_inventory(working_root)
    if staged_files != working_files:
        raise SiteValidationError("Staged files do not match the working-site file set.")
    if staged_dirs != working_dirs:
        raise SiteValidationError("Staged directories do not match the working-site directory set.")

    for relative in sorted(working_files):
        if _is_editable_file(relative):
            continue
        if _sha256_file(working_root / relative) != _sha256_file(staged_root / relative):
            raise SiteValidationError(
                f"Staged asset must remain byte-for-byte unchanged: {relative.as_posix()}"
            )


def validate_site_structure(site_root: Path) -> None:
    """Validate a fixture, working, or staged tree as the fixed static site."""
    _site_inventory(site_root)


def read_file(
    handle: WorkspaceHandle,
    *,
    file: str,
    start_line: int = 1,
    end_line: int | None = None,
    limits: WorkspaceLimits = DEFAULT_WORKSPACE_LIMITS,
) -> FileReadResult:
    """Read a bounded one-based line range from an approved staged text file."""
    validate_staged_site(handle)
    path = _resolve_tool_file(handle, file)
    if start_line < 1:
        raise FileToolError("start_line must be at least 1.")
    requested_end_line = end_line if end_line is not None else start_line + limits.max_read_lines - 1
    if requested_end_line < start_line:
        raise FileToolError("end_line must be greater than or equal to start_line.")
    if requested_end_line - start_line + 1 > limits.max_read_lines:
        raise FileToolError(
            f"read_file is limited to {limits.max_read_lines} lines per call."
        )

    raw_content = _read_bounded_bytes(path, limits.max_file_bytes, file)
    try:
        content = raw_content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise FileToolError(f"Cannot read {file}: file is not valid UTF-8.") from exc

    lines = content.splitlines(keepends=True)
    total_lines = len(lines)
    if total_lines == 0:
        raise FileToolError("Cannot read an empty file.")
    if start_line > total_lines:
        raise FileToolError("start_line is beyond the end of the file.")

    actual_end_line = min(requested_end_line, total_lines)
    return FileReadResult(
        file=file,
        start_line=start_line,
        end_line=actual_end_line,
        total_lines=total_lines,
        content="".join(lines[start_line - 1 : actual_end_line]),
        truncated=requested_end_line < total_lines,
    )


def propose_patch(
    handle: WorkspaceHandle,
    *,
    specialist: SpecialistName | str,
    file: str,
    old_text: str,
    new_text: str,
    summary: str,
    limits: WorkspaceLimits = DEFAULT_WORKSPACE_LIMITS,
    replace_function: ReplaceFunction | None = None,
    validation_function: ValidationFunction | None = None,
) -> PatchExecutionResult:
    """Apply one exact unique replacement atomically or return a safe rejection."""
    try:
        trusted_specialist = SpecialistName(specialist)
    except ValueError as exc:
        raise FileToolError("Trusted specialist identity is invalid.") from exc

    safe_file = _safe_result_file(file)
    safe_summary = _safe_result_summary(summary)
    old_size = len(old_text.encode("utf-8"))
    new_size = len(new_text.encode("utf-8"))
    if old_size > limits.max_old_text_bytes or new_size > limits.max_new_text_bytes:
        return _rejected_patch(
            specialist=trusted_specialist,
            file=safe_file,
            summary=safe_summary,
            reason=PatchRejectionReason.PATCH_TOO_LARGE,
            message="Patch text exceeds the configured byte limit.",
        )
    if old_text == new_text:
        return _rejected_patch(
            specialist=trusted_specialist,
            file=safe_file,
            summary=safe_summary,
            reason=PatchRejectionReason.NO_OP,
            message="Patch replacement must change the target text.",
        )

    target_rejection = _validate_patch_target(file, trusted_specialist)
    if target_rejection is not None:
        reason, message = target_rejection
        return _rejected_patch(
            specialist=trusted_specialist,
            file=safe_file,
            summary=safe_summary,
            reason=reason,
            message=message,
        )

    try:
        proposal = PatchProposal(
            agent=trusted_specialist,
            file=file,
            old_text=old_text,
            new_text=new_text,
            summary=summary,
        )
    except (ValueError, ValidationError):
        return _rejected_patch(
            specialist=trusted_specialist,
            file=safe_file,
            summary=safe_summary,
            reason=PatchRejectionReason.INVALID_PATCH,
            message="Patch proposal fields are invalid.",
        )

    validator = validation_function or validate_staged_site
    validator(handle)
    try:
        path = _resolve_tool_file(handle, proposal.file)
    except FileToolError as exc:
        reason = (
            PatchRejectionReason.FILE_NOT_FOUND
            if "does not exist" in str(exc)
            else PatchRejectionReason.UNSAFE_PATH
        )
        return _rejected_patch(
            specialist=trusted_specialist,
            file=proposal.file,
            summary=proposal.summary,
            reason=reason,
            message=str(exc),
        )

    try:
        original_bytes = _read_bounded_bytes(path, limits.max_file_bytes, proposal.file)
    except _FileSizeLimitError:
        return _rejected_patch(
            specialist=trusted_specialist,
            file=proposal.file,
            summary=proposal.summary,
            reason=PatchRejectionReason.FILE_TOO_LARGE,
            message=f"Cannot patch {proposal.file}: file exceeds the configured byte limit.",
        )

    before_hash = _sha256_bytes(original_bytes)
    try:
        content = original_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return _rejected_patch(
            specialist=trusted_specialist,
            file=proposal.file,
            summary=proposal.summary,
            reason=PatchRejectionReason.INVALID_ENCODING,
            message=f"Cannot patch {proposal.file}: file is not valid UTF-8.",
            bytes_before=len(original_bytes),
            before_sha256=before_hash,
        )

    match_positions = find_exact_match_positions(content, proposal.old_text)
    if not match_positions:
        return _rejected_patch(
            specialist=trusted_specialist,
            file=proposal.file,
            summary=proposal.summary,
            reason=PatchRejectionReason.TARGET_NOT_FOUND,
            message="old_text was not found in the staged file.",
            match_count=0,
            bytes_before=len(original_bytes),
            before_sha256=before_hash,
        )
    if len(match_positions) > 1:
        return _rejected_patch(
            specialist=trusted_specialist,
            file=proposal.file,
            summary=proposal.summary,
            reason=PatchRejectionReason.AMBIGUOUS_TARGET,
            message="old_text matches more than once, including overlapping matches.",
            match_count=len(match_positions),
            bytes_before=len(original_bytes),
            before_sha256=before_hash,
        )

    position = match_positions[0]
    modified = content[:position] + proposal.new_text + content[position + len(proposal.old_text) :]
    modified_bytes = modified.encode("utf-8")
    if modified_bytes == original_bytes:
        return _rejected_patch(
            specialist=trusted_specialist,
            file=proposal.file,
            summary=proposal.summary,
            reason=PatchRejectionReason.NO_OP,
            message="Patch replacement would not change the staged file.",
            match_count=1,
            bytes_before=len(original_bytes),
            before_sha256=before_hash,
        )
    if len(modified_bytes) > limits.max_file_bytes:
        return _rejected_patch(
            specialist=trusted_specialist,
            file=proposal.file,
            summary=proposal.summary,
            reason=PatchRejectionReason.PATCH_TOO_LARGE,
            message="Patched file would exceed the configured byte limit.",
            match_count=1,
            bytes_before=len(original_bytes),
            before_sha256=before_hash,
        )

    replacer = replace_function or os.replace
    try:
        _atomic_replace_bytes(path, modified_bytes, replacer)
    except OSError as exc:
        if getattr(exc, "replacement_completed", False):
            try:
                _atomic_replace_bytes(path, original_bytes, replacer)
            except OSError as rollback_exc:
                raise PatchRollbackError(
                    f"Atomic patch verification failed and original bytes could not be restored "
                    f"for {proposal.file}; staged workspace may require cleanup."
                ) from rollback_exc
            raise PatchApplicationError(
                f"Atomic patch verification failed for {proposal.file}; original staged bytes "
                "were restored."
            ) from exc
        raise PatchApplicationError(f"Atomic replacement failed for {proposal.file}.") from exc

    try:
        validator(handle)
    except Exception as validation_exc:
        try:
            _atomic_replace_bytes(path, original_bytes, replacer)
        except OSError as rollback_exc:
            raise PatchRollbackError(
                f"Patch validation failed and original bytes could not be restored for "
                f"{proposal.file}; staged workspace may require cleanup."
            ) from rollback_exc
        try:
            validator(handle)
        except Exception as restore_validation_exc:
            raise PatchRollbackError(
                f"Patch validation failed and restoration could not be verified for "
                f"{proposal.file}; staged workspace may require cleanup."
            ) from restore_validation_exc
        raise PatchApplicationError(
            f"Patch validation failed for {proposal.file}; original staged bytes were restored."
        ) from validation_exc

    return PatchExecutionResult(
        status=PatchStatus.APPLIED,
        file=proposal.file,
        specialist=proposal.agent.value,
        summary=proposal.summary,
        match_count=1,
        replacements=1,
        bytes_before=len(original_bytes),
        bytes_after=len(modified_bytes),
        before_sha256=before_hash,
        after_sha256=_sha256_bytes(modified_bytes),
        rejection_reason=None,
        message="Patch applied atomically to the staged file.",
    )


def find_exact_match_positions(content: str, target: str) -> list[int]:
    """Return every exact target start position, including overlapping matches."""
    if not target:
        raise ValueError("target must not be empty.")
    positions: list[int] = []
    start = 0
    while True:
        position = content.find(target, start)
        if position < 0:
            return positions
        positions.append(position)
        start = position + 1


def generate_diff(
    handle: WorkspaceHandle,
    *,
    settings: Settings | None = None,
    limits: WorkspaceLimits = DEFAULT_WORKSPACE_LIMITS,
) -> DiffReport:
    """Generate deterministic per-file and combined diffs against working."""
    resolved_settings = settings or get_settings()
    working_root = resolved_settings.working_site_dir
    validate_site_structure(working_root)
    validate_staged_site(handle)
    if handle.source_working_path is None or handle.source_working_path.resolve() != working_root.resolve():
        raise WorkspaceError("Workspace baseline does not match the configured working site.")

    working_files, _ = _site_inventory(working_root)
    file_diffs: list[FileDiff] = []
    combined_bytes = 0
    for relative_path in sorted(path for path in working_files if _is_editable_file(path)):
        relative_name = relative_path.as_posix()
        try:
            working_bytes = _read_bounded_bytes(
                working_root / relative_path,
                limits.max_file_bytes,
                relative_name,
            )
            staged_bytes = _read_bounded_bytes(
                handle.path / relative_path,
                limits.max_file_bytes,
                relative_name,
            )
        except _FileSizeLimitError as exc:
            raise SiteValidationError(
                f"Cannot generate diff for {relative_name}: file exceeds the configured byte limit."
            ) from exc
        if working_bytes == staged_bytes:
            continue
        try:
            working_text = working_bytes.decode("utf-8", errors="strict")
            staged_text = staged_bytes.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise SiteValidationError(
                f"Cannot generate diff for {relative_name}: file is not valid UTF-8."
            ) from exc

        working_lines = working_text.splitlines(keepends=True)
        staged_lines = staged_text.splitlines(keepends=True)
        unified_diff = _build_unified_diff(relative_name, working_lines, staged_lines)
        added_lines, removed_lines = _count_changed_lines(working_lines, staged_lines)
        combined_bytes += len(unified_diff.encode("utf-8"))
        if combined_bytes > limits.max_combined_diff_bytes:
            raise SiteValidationError("Combined diff exceeds the configured byte limit.")
        file_diffs.append(
            FileDiff(
                file=relative_name,
                unified_diff=unified_diff,
                added_lines=added_lines,
                removed_lines=removed_lines,
            )
        )

    changed_files = [file.file for file in file_diffs]
    return DiffReport(
        run_id=handle.run_id,
        changed_files=changed_files,
        files=file_diffs,
        combined_diff="".join(file.unified_diff for file in file_diffs),
        is_empty=not file_diffs,
        total_added_lines=sum(file.added_lines for file in file_diffs),
        total_removed_lines=sum(file.removed_lines for file in file_diffs),
    )


def _uuid_run_id() -> str:
    return uuid.uuid4().hex


def _validate_handle(handle: WorkspaceHandle) -> WorkspaceHandle:
    try:
        return WorkspaceHandle.model_validate(handle)
    except ValidationError as exc:
        raise WorkspaceError("Invalid workspace handle.") from exc


def _validate_staging_root(staging_root: Path) -> Path:
    if staging_root.is_symlink():
        raise WorkspaceError("Staging root must not be a symlink.")
    try:
        resolved = staging_root.resolve(strict=True)
    except FileNotFoundError as exc:
        raise WorkspaceError("Staging root does not exist.") from exc
    if not resolved.is_dir():
        raise WorkspaceError("Staging root is not a directory.")
    return resolved


def _validate_workspace_boundary(handle: WorkspaceHandle, *, require_exists: bool) -> Path:
    staging_root = _validate_staging_root(handle.staging_root)
    candidate = handle.path
    if candidate.parent != handle.staging_root or candidate.name != handle.run_id:
        raise WorkspaceError("Workspace path must be one direct child of staging root.")
    if candidate == handle.staging_root:
        raise WorkspaceError("Workspace path must not be the staging root.")
    if candidate.is_symlink():
        raise WorkspaceError("Staged workspace must not be a symlink.")
    if not candidate.exists():
        if require_exists:
            raise WorkspaceError(f"Staged workspace does not exist for run_id: {handle.run_id}")
        return candidate
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(staging_root)
    except (FileNotFoundError, ValueError) as exc:
        raise WorkspaceError("Workspace path escapes the staging root.") from exc
    if resolved.parent != staging_root or not resolved.is_dir():
        raise WorkspaceError("Workspace path must be a direct staged-run directory.")
    return resolved


def _remove_run_directory(handle: WorkspaceHandle) -> None:
    candidate = handle.path
    if candidate.is_symlink():
        raise WorkspaceError("Refusing to clean a symlinked staged workspace.")
    if candidate.exists():
        shutil.rmtree(candidate)


def _site_inventory(site_root: Path) -> tuple[set[Path], set[Path]]:
    root = Path(os.path.abspath(site_root))
    if root.is_symlink():
        raise SiteValidationError("Site root must not be a symlink.")
    try:
        root = root.resolve(strict=True)
    except FileNotFoundError as exc:
        raise SiteValidationError("Site root does not exist.") from exc
    if not root.is_dir():
        raise SiteValidationError("Site root is not a directory.")

    seen_files: set[Path] = set()
    seen_dirs: set[Path] = set()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_symlink():
            raise SiteValidationError(f"Symlink is not allowed in site tree: {relative.as_posix()}")
        if path.is_dir():
            seen_dirs.add(relative)
            continue
        if not path.is_file():
            raise SiteValidationError(
                f"Unsupported filesystem entry in site tree: {relative.as_posix()}"
            )
        if _is_editable_file(relative):
            seen_files.add(relative)
            continue
        if relative.parts[0] != "assets" or relative.suffix.lower() == ".js":
            raise SiteValidationError(
                f"Unsupported file in static site tree: {relative.as_posix()}"
            )
        seen_files.add(relative)

    if not REQUIRED_EDITABLE_FILES.issubset(seen_files):
        raise SiteValidationError("Site is missing one or more required HTML/CSS files.")
    if Path("assets") not in seen_dirs:
        raise SiteValidationError("Site must contain the assets directory.")
    if any(directory.parts[0] != "assets" for directory in seen_dirs):
        raise SiteValidationError("Only the assets directory may be nested in the site tree.")
    return seen_files, seen_dirs


def _is_editable_file(relative: Path) -> bool:
    return len(relative.parts) == 1 and relative.suffix.lower() in APPROVED_EDIT_EXTENSIONS


def _resolve_tool_file(handle: WorkspaceHandle, file: str) -> Path:
    validated = _validate_handle(handle)
    workspace_root = _validate_workspace_boundary(validated, require_exists=True)
    _validate_simple_edit_file(file)
    candidate = validated.path / file
    if candidate.is_symlink():
        raise FileToolError(f"Cannot access {file}: staged file must not be a symlink.")
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise FileToolError(f"file does not exist in staged workspace: {file}") from exc
    try:
        resolved.relative_to(workspace_root)
    except ValueError as exc:
        raise FileToolError("file must stay inside the staged workspace.") from exc
    if resolved.parent != workspace_root or not resolved.is_file():
        raise FileToolError(f"file does not exist in staged workspace: {file}")
    return resolved


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
    if path.suffix.lower() not in APPROVED_EDIT_EXTENSIONS:
        raise FileToolError("file path must target an approved .html or .css file.")


def _validate_patch_target(
    file: str,
    specialist: SpecialistName,
) -> tuple[PatchRejectionReason, str] | None:
    try:
        _validate_simple_edit_file(file)
    except FileToolError as exc:
        suffix = PurePath(file).suffix.lower() if isinstance(file, str) else ""
        reason = (
            PatchRejectionReason.UNSUPPORTED_EXTENSION
            if suffix and suffix not in APPROVED_EDIT_EXTENSIONS
            else PatchRejectionReason.UNSAFE_PATH
        )
        return reason, str(exc)
    suffix = PurePath(file).suffix.lower()
    if specialist is SpecialistName.CSS and suffix != ".css":
        return (
            PatchRejectionReason.UNAUTHORIZED_FILE,
            "CSS specialist may patch only staged CSS files.",
        )
    if specialist in {SpecialistName.HTML, SpecialistName.SEO} and suffix != ".html":
        return (
            PatchRejectionReason.UNAUTHORIZED_FILE,
            "HTML and SEO specialists may patch only staged HTML files.",
        )
    return None


def _read_bounded_bytes(path: Path, max_bytes: int, display_name: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise FileToolError(f"Cannot read staged file: {display_name}") from exc
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise FileToolError(f"Staged target is not a regular file: {display_name}")
        if file_stat.st_size > max_bytes:
            raise _FileSizeLimitError(
                f"Cannot process {display_name}: file exceeds the configured byte limit."
            )
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            content = stream.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise _FileSizeLimitError(
                f"Cannot process {display_name}: file exceeds the configured byte limit."
            )
        return content
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _atomic_replace_bytes(
    target: Path,
    content: bytes,
    replace_function: ReplaceFunction,
) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.agentorchestra-",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    replacement_completed = False
    try:
        target_mode = stat.S_IMODE(target.stat(follow_symlinks=False).st_mode)
        os.fchmod(descriptor, target_mode)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        replace_function(temporary_path, target)
        replacement_completed = True
        if _read_installed_bytes(target, expected_size=len(content)) != content:
            raise OSError("Atomic replacement did not install the expected bytes.")
    except OSError as exc:
        raise _AtomicWriteError(
            str(exc),
            replacement_completed=replacement_completed,
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)


def _read_installed_bytes(target: Path, *, expected_size: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(target, flags)
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size != expected_size:
            raise OSError("Atomic replacement produced an unexpected target file.")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            return stream.read(expected_size + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _rejected_patch(
    *,
    specialist: SpecialistName,
    file: str,
    summary: str,
    reason: PatchRejectionReason,
    message: str,
    match_count: int | None = None,
    bytes_before: int | None = None,
    before_sha256: str | None = None,
) -> PatchExecutionResult:
    return PatchExecutionResult(
        status=PatchStatus.REJECTED,
        file=file,
        specialist=specialist.value,
        summary=summary,
        match_count=match_count,
        replacements=0,
        bytes_before=bytes_before,
        bytes_after=bytes_before,
        before_sha256=before_sha256,
        after_sha256=before_sha256,
        rejection_reason=reason,
        message=message,
    )


def _safe_result_file(file: object) -> str:
    if isinstance(file, str) and 0 < len(file) <= 120:
        return file
    return "<invalid>"


def _safe_result_summary(summary: object) -> str:
    if isinstance(summary, str):
        stripped = summary.strip()
        if 0 < len(stripped) <= 300:
            return stripped
    return "Invalid patch proposal."


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SiteValidationError("Unable to safely validate immutable site asset.") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise SiteValidationError("Immutable site asset is not a regular file.")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            for chunk in iter(lambda: stream.read(64 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _build_unified_diff(
    relative_name: str,
    working_lines: list[str],
    staged_lines: list[str],
) -> str:
    lines = difflib.unified_diff(
        working_lines,
        staged_lines,
        fromfile=f"working/{relative_name}",
        tofile=f"staging/{relative_name}",
        lineterm="\n",
    )
    return "".join(line if line.endswith("\n") else f"{line}\n" for line in lines)


def _count_changed_lines(working_lines: list[str], staged_lines: list[str]) -> tuple[int, int]:
    added = 0
    removed = 0
    matcher = difflib.SequenceMatcher(a=working_lines, b=staged_lines, autojunk=False)
    for operation, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if operation in {"replace", "delete"}:
            removed += old_end - old_start
        if operation in {"replace", "insert"}:
            added += new_end - new_start
    return added, removed
