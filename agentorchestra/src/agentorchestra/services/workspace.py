from __future__ import annotations

import difflib
import hashlib
import os
import re
import shutil
import stat
import tempfile
import uuid
from collections import Counter
from collections.abc import Callable, Collection
from pathlib import Path, PurePath

from pydantic import ValidationError

from agentorchestra.config import Settings, ensure_runtime_directories, get_settings
from agentorchestra.exceptions import DomainValidationError
from agentorchestra.models import PatchProposal, SpecialistName
from agentorchestra.services.patch_policy import validate_specialist_patch
from agentorchestra.services.transaction_lock import working_site_transaction
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
_BASELINE_METADATA_PREFIX = ".agentorchestra-baseline-"

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

    run_id = (run_id_factory or _uuid_run_id)()
    handle: WorkspaceHandle | None = None
    cleanup_required = False
    try:
        with working_site_transaction(resolved_settings):
            from agentorchestra.services.site_digest import compute_site_tree_digest

            validate_site_structure(working_path)
            _validate_staging_root(staging_root)
            source_digest = compute_site_tree_digest(working_path).digest
            try:
                handle = WorkspaceHandle(
                    run_id=run_id,
                    path=staging_root / run_id,
                    staging_root=staging_root,
                    source_working_path=working_path,
                    source_working_digest=source_digest,
                )
            except ValidationError as exc:
                raise WorkspaceError("Generated run ID or staged path is invalid.") from exc
            _validate_workspace_boundary(handle, require_exists=False)
            metadata_path = _baseline_metadata_path(handle)
            if (
                handle.path.exists()
                or handle.path.is_symlink()
                or metadata_path.exists()
                or metadata_path.is_symlink()
            ):
                raise WorkspaceError(f"Staged workspace already exists for run_id: {run_id}")

            copier = copy_function or shutil.copytree
            cleanup_required = True
            copier(working_path, handle.path, symlinks=False)
            validate_staged_site(handle)
            if compute_site_tree_digest(handle.path).digest != source_digest:
                raise WorkspaceError("Staged workspace does not match its working-site baseline.")
            _write_baseline_metadata(metadata_path, source_digest)
    except Exception as exc:
        if not cleanup_required or handle is None:
            if isinstance(exc, WorkspaceError):
                raise
            raise WorkspaceError(f"Failed to create staged workspace for run_id: {run_id}") from exc
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
        provisional = WorkspaceHandle(
            run_id=run_id,
            path=staging_root / run_id,
            staging_root=staging_root,
            source_working_path=resolved_settings.working_site_dir,
        )
        source_digest = _read_baseline_metadata(_baseline_metadata_path(provisional))
        handle = provisional.model_copy(update={"source_working_digest": source_digest})
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
    allowed_files: Collection[str] | None = None,
    limits: WorkspaceLimits = DEFAULT_WORKSPACE_LIMITS,
) -> FileReadResult:
    """Read a bounded one-based line range from an approved staged text file."""
    validate_staged_site(handle)
    approved_scope = normalize_allowed_files(allowed_files)
    if approved_scope is not None and file not in approved_scope:
        raise FileToolError(f"file is outside this assignment's approved read scope: {file}")
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

    lines = _normalize_newlines(content).splitlines(keepends=True)
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
    allowed_files: Collection[str] | None = None,
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
    approved_scope = normalize_allowed_files(allowed_files)
    if approved_scope is not None and file not in approved_scope:
        return _rejected_patch(
            specialist=trusted_specialist,
            file=safe_file,
            summary=safe_summary,
            reason=PatchRejectionReason.UNAUTHORIZED_FILE,
            message="File is outside this assignment's approved patch scope.",
        )
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

    old_text = _adapt_to_file_newlines(content, proposal.old_text)
    new_text = _adapt_to_file_newlines(content, proposal.new_text)
    match_positions = find_exact_match_positions(content, old_text)
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
    modified = content[:position] + new_text + content[position + len(old_text) :]
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

    if trusted_specialist is SpecialistName.CSS:
        duplicate = _introduced_duplicate_css_declaration(content, modified)
        if duplicate is not None:
            selector, property_name = duplicate
            return _rejected_patch(
                specialist=trusted_specialist,
                file=proposal.file,
                summary=proposal.summary,
                reason=PatchRejectionReason.INVALID_PATCH,
                message=(
                    f"CSS patch would introduce duplicate property {property_name!r} in "
                    f"selector {selector!r}. This write was rejected and nothing changed. "
                    "Do not return completed; call update_css_declaration with this exact "
                    "selector and property to update the existing declaration."
                ),
                match_count=1,
                bytes_before=len(original_bytes),
                before_sha256=before_hash,
            )

    ownership_error = validate_specialist_patch(content, modified, trusted_specialist)
    if ownership_error is not None:
        return _rejected_patch(
            specialist=trusted_specialist,
            file=proposal.file,
            summary=proposal.summary,
            reason=PatchRejectionReason.OWNERSHIP_VIOLATION,
            message=ownership_error,
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


def update_css_declaration(
    handle: WorkspaceHandle,
    *,
    specialist: SpecialistName | str,
    selector: str,
    property_name: str,
    value: str,
    summary: str,
    allowed_files: Collection[str] | None = None,
    limits: WorkspaceLimits = DEFAULT_WORKSPACE_LIMITS,
    replace_function: ReplaceFunction | None = None,
    validation_function: ValidationFunction | None = None,
) -> PatchExecutionResult:
    """Update one existing declaration in one uniquely matched simple CSS rule.

    The final mutation passes through ``propose_patch`` so exact matching,
    ownership checks, atomic replacement, rollback, and staged-site validation
    remain the source of truth.
    """
    try:
        trusted_specialist = SpecialistName(specialist)
    except ValueError as exc:
        raise FileToolError("Trusted specialist identity is invalid.") from exc
    if trusted_specialist is not SpecialistName.CSS:
        raise FileToolError("Structured CSS updates require the CSS specialist.")

    file = "style.css"
    safe_summary = _safe_result_summary(summary)
    selector = selector.strip() if isinstance(selector, str) else ""
    property_name = property_name.strip() if isinstance(property_name, str) else ""
    value = value.strip() if isinstance(value, str) else ""
    invalid_selector = (
        not 0 < len(selector) <= 200
        or any(character in selector for character in "{};\r\n")
        or "/*" in selector
    )
    invalid_property = not re.fullmatch(r"(?:--)?[A-Za-z][A-Za-z0-9-]{0,79}", property_name)
    invalid_value = (
        not 0 < len(value) <= 500
        or any(character in value for character in ";{}\r\n")
        or "/*" in value
        or "*/" in value
    )
    if invalid_selector or invalid_property or invalid_value:
        return _rejected_patch(
            specialist=trusted_specialist,
            file=file,
            summary=safe_summary,
            reason=PatchRejectionReason.INVALID_PATCH,
            message="Structured CSS selector, property, or value is invalid.",
        )
    approved_scope = normalize_allowed_files(allowed_files)
    if approved_scope is not None and file not in approved_scope:
        return _rejected_patch(
            specialist=trusted_specialist,
            file=file,
            summary=safe_summary,
            reason=PatchRejectionReason.UNAUTHORIZED_FILE,
            message="style.css is outside this assignment's approved patch scope.",
        )

    validator = validation_function or validate_staged_site
    validator(handle)
    try:
        path = _resolve_tool_file(handle, file)
        original_bytes = _read_bounded_bytes(path, limits.max_file_bytes, file)
    except _FileSizeLimitError:
        return _rejected_patch(
            specialist=trusted_specialist,
            file=file,
            summary=safe_summary,
            reason=PatchRejectionReason.FILE_TOO_LARGE,
            message=f"Cannot patch {file}: file exceeds the configured byte limit.",
        )
    except FileToolError as exc:
        reason = (
            PatchRejectionReason.FILE_NOT_FOUND
            if "does not exist" in str(exc)
            else PatchRejectionReason.UNSAFE_PATH
        )
        return _rejected_patch(
            specialist=trusted_specialist,
            file=file,
            summary=safe_summary,
            reason=reason,
            message=str(exc),
        )

    before_hash = _sha256_bytes(original_bytes)
    try:
        content = original_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return _rejected_patch(
            specialist=trusted_specialist,
            file=file,
            summary=safe_summary,
            reason=PatchRejectionReason.INVALID_ENCODING,
            message=f"Cannot patch {file}: file is not valid UTF-8.",
            bytes_before=len(original_bytes),
            before_sha256=before_hash,
        )

    code_mask = _css_code_mask(content)
    rule_matches = _find_simple_css_rules(content, selector, code_mask=code_mask)
    if not rule_matches:
        resolved_selector = _resolve_unique_css_selector_extension(
            content,
            selector,
            code_mask=code_mask,
        )
        if resolved_selector is not None:
            selector = resolved_selector
            rule_matches = _find_simple_css_rules(content, selector, code_mask=code_mask)
    if not rule_matches:
        return _rejected_patch(
            specialist=trusted_specialist,
            file=file,
            summary=safe_summary,
            reason=PatchRejectionReason.TARGET_NOT_FOUND,
            message=f"CSS selector {selector!r} was not found as a simple rule.",
            match_count=0,
            bytes_before=len(original_bytes),
            before_sha256=before_hash,
        )
    if len(rule_matches) > 1:
        return _rejected_patch(
            specialist=trusted_specialist,
            file=file,
            summary=safe_summary,
            reason=PatchRejectionReason.AMBIGUOUS_TARGET,
            message=f"CSS selector {selector!r} matches more than one rule.",
            match_count=len(rule_matches),
            bytes_before=len(original_bytes),
            before_sha256=before_hash,
        )

    rule_start, body_start, body_end, rule_end = rule_matches[0]
    body = content[body_start:body_end]
    declarations = _find_simple_css_declarations(
        body,
        property_name,
        content_offset=body_start,
        code_mask=code_mask,
    )
    if not declarations and property_name.casefold() == "background-color":
        background_declarations = _find_simple_css_declarations(
            body,
            "background",
            content_offset=body_start,
            code_mask=code_mask,
        )
        if len(background_declarations) == 1:
            background_start, background_end = background_declarations[0]
            existing_background = body[background_start:background_end].strip()
            if _is_color_only_css_value(existing_background):
                property_name = "background"
                declarations = background_declarations
    if not declarations and property_name.casefold() == "height":
        height_candidates: list[tuple[str, list[tuple[int, int]]]] = []
        for candidate_name in ("min-height", "max-height"):
            candidate_declarations = _find_simple_css_declarations(
                body,
                candidate_name,
                content_offset=body_start,
                code_mask=code_mask,
            )
            if len(candidate_declarations) == 1:
                height_candidates.append((candidate_name, candidate_declarations))
        if len(height_candidates) == 1:
            property_name, declarations = height_candidates[0]
    if not declarations:
        return _rejected_patch(
            specialist=trusted_specialist,
            file=file,
            summary=safe_summary,
            reason=PatchRejectionReason.TARGET_NOT_FOUND,
            message=(
                f"CSS property {property_name!r} was not found as a single-line declaration "
                f"in selector {selector!r}."
            ),
            match_count=0,
            bytes_before=len(original_bytes),
            before_sha256=before_hash,
        )
    if len(declarations) > 1:
        return _rejected_patch(
            specialist=trusted_specialist,
            file=file,
            summary=safe_summary,
            reason=PatchRejectionReason.AMBIGUOUS_TARGET,
            message=f"CSS property {property_name!r} appears more than once in {selector!r}.",
            match_count=len(declarations),
            bytes_before=len(original_bytes),
            before_sha256=before_hash,
        )

    value_start, value_end = declarations[0]
    current_value = body[value_start:value_end].strip()
    if current_value == value:
        return _rejected_patch(
            specialist=trusted_specialist,
            file=file,
            summary=safe_summary,
            reason=PatchRejectionReason.NO_OP,
            message=f"CSS property {property_name!r} already has the requested value.",
            match_count=1,
            bytes_before=len(original_bytes),
            before_sha256=before_hash,
        )

    old_rule = content[rule_start:rule_end]
    relative_start = body_start - rule_start + value_start
    relative_end = body_start - rule_start + value_end
    new_rule = old_rule[:relative_start] + value + old_rule[relative_end:]
    return propose_patch(
        handle,
        specialist=trusted_specialist,
        file=file,
        old_text=old_rule,
        new_text=new_rule,
        summary=safe_summary,
        allowed_files=approved_scope,
        limits=limits,
        replace_function=replace_function,
        validation_function=validator,
    )


def _css_code_mask(content: str) -> bytearray:
    """Mark characters outside CSS comments and quoted strings."""
    mask = bytearray([1]) * len(content)
    index = 0
    state: str | None = None
    while index < len(content):
        if state == "comment":
            mask[index] = 0
            if content.startswith("*/", index):
                if index + 1 < len(content):
                    mask[index + 1] = 0
                index += 2
                state = None
            else:
                index += 1
            continue
        if state in {"'", '"'}:
            mask[index] = 0
            if content[index] == "\\" and index + 1 < len(content):
                mask[index + 1] = 0
                index += 2
                continue
            if content[index] == state:
                state = None
            index += 1
            continue
        if content.startswith("/*", index):
            mask[index] = 0
            if index + 1 < len(content):
                mask[index + 1] = 0
            state = "comment"
            index += 2
            continue
        if content[index] in {"'", '"'}:
            mask[index] = 0
            state = content[index]
        index += 1
    return mask


def _find_simple_css_rules(
    content: str,
    selector: str,
    *,
    code_mask: bytearray,
) -> list[tuple[int, int, int, int]]:
    selector_pattern = re.compile(
        rf"(?m)^[ \t]*{re.escape(selector)}[ \t]*\{{[ \t]*(?:\r?\n|$)"
    )
    matches: list[tuple[int, int, int, int]] = []
    for match in selector_pattern.finditer(content):
        rule_start = match.start()
        open_brace = content.find("{", rule_start, match.end())
        if open_brace < 0 or not code_mask[rule_start] or not code_mask[open_brace]:
            continue
        close_brace = _matching_css_brace(content, open_brace, code_mask=code_mask)
        if close_brace is None:
            continue
        body_start = open_brace + 1
        matches.append((rule_start, body_start, close_brace, close_brace + 1))
    return matches


def _resolve_unique_css_selector_extension(
    content: str,
    selector: str,
    *,
    code_mask: bytearray,
) -> str | None:
    """Resolve a shortened class only when one direct simple-rule extension exists."""
    if not re.fullmatch(r"\.[A-Za-z_][A-Za-z0-9_-]*", selector):
        return None
    selector_pattern = re.compile(
        r"(?m)^[ \t]*(?P<selector>\.[A-Za-z_][A-Za-z0-9_-]*)[ \t]*\{"
    )
    candidates: set[str] = set()
    for match in selector_pattern.finditer(content):
        if not code_mask[match.start("selector")]:
            continue
        candidate = match.group("selector")
        if candidate.startswith(f"{selector}-") or candidate.startswith(f"{selector}_"):
            candidates.add(candidate)
    if len(candidates) == 1:
        return candidates.pop()
    return None


def _matching_css_brace(
    content: str,
    open_brace: int,
    *,
    code_mask: bytearray,
) -> int | None:
    depth = 0
    for index in range(open_brace, len(content)):
        if not code_mask[index]:
            continue
        if content[index] == "{":
            depth += 1
        elif content[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _find_simple_css_declarations(
    body: str,
    property_name: str,
    *,
    content_offset: int,
    code_mask: bytearray,
) -> list[tuple[int, int]]:
    flags = re.MULTILINE if property_name.startswith("--") else re.IGNORECASE | re.MULTILINE
    declaration_pattern = re.compile(
        rf"^(?P<prefix>[ \t]*{re.escape(property_name)}[ \t]*:[ \t]*)"
        rf"(?P<value>[^;\r\n{{}}]*?)(?P<suffix>[ \t]*;[ \t]*(?:\r?$|\n))",
        flags,
    )
    matches: list[tuple[int, int]] = []
    for match in declaration_pattern.finditer(body):
        absolute_start = content_offset + match.start("prefix")
        if absolute_start >= len(code_mask) or not code_mask[absolute_start]:
            continue
        matches.append(match.span("value"))
    return matches


def _is_color_only_css_value(value: str) -> bool:
    """Return whether a background shorthand is narrow enough for a color update."""
    stripped = value.strip()
    if re.fullmatch(r"#[0-9a-fA-F]{3,8}", stripped):
        return True
    if re.fullmatch(r"var\(\s*--[A-Za-z0-9-]+(?:\s*,[^()]*)?\s*\)", stripped):
        return True
    if re.fullmatch(
        r"(?:rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch|color)\([^{};]*\)",
        stripped,
        flags=re.IGNORECASE,
    ):
        return True
    return bool(re.fullmatch(r"[A-Za-z]+", stripped))


def _introduced_duplicate_css_declaration(
    before: str,
    after: str,
) -> tuple[str, str] | None:
    introduced = _css_duplicate_declarations(after) - _css_duplicate_declarations(before)
    if not introduced:
        return None
    selector, property_name = sorted(introduced.elements())[0]
    return selector, property_name


def _css_duplicate_declarations(content: str) -> Counter[tuple[str, str]]:
    """Count duplicate properties inside simple rules, ignoring nested at-rules."""
    code_mask = _css_code_mask(content)
    duplicates: Counter[tuple[str, str]] = Counter()
    for open_brace, character in enumerate(content):
        if character != "{" or not code_mask[open_brace]:
            continue
        close_brace = _matching_css_brace(content, open_brace, code_mask=code_mask)
        if close_brace is None:
            continue
        body = content[open_brace + 1 : close_brace]
        if any(
            code_mask[open_brace + 1 + offset] and token in "{}"
            for offset, token in enumerate(body)
        ):
            continue
        boundary = max(
            content.rfind("}", 0, open_brace),
            content.rfind("{", 0, open_brace),
            content.rfind(";", 0, open_brace),
        )
        selector = " ".join(content[boundary + 1 : open_brace].split())
        if not selector or selector.startswith("@"):
            continue
        properties: Counter[str] = Counter()
        declaration_pattern = re.compile(
            r"(?:^|[;\n])\s*(?P<property>(?:--)?[A-Za-z][A-Za-z0-9-]*)\s*:",
            flags=re.MULTILINE,
        )
        for match in declaration_pattern.finditer(body):
            absolute_start = open_brace + 1 + match.start("property")
            if not code_mask[absolute_start]:
                continue
            property_name = match.group("property")
            normalized = property_name if property_name.startswith("--") else property_name.casefold()
            properties[normalized] += 1
        for property_name, count in properties.items():
            if count > 1:
                duplicates[(selector, property_name)] += count - 1
    return duplicates


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

        working_lines = _normalize_newlines(working_text).splitlines(keepends=True)
        staged_lines = _normalize_newlines(staged_text).splitlines(keepends=True)
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
    metadata = _baseline_metadata_path(handle)
    if candidate.is_symlink():
        raise WorkspaceError("Refusing to clean a symlinked staged workspace.")
    if metadata.is_symlink():
        raise WorkspaceError("Refusing to clean symlinked workspace baseline metadata.")
    if candidate.exists():
        shutil.rmtree(candidate)
    metadata.unlink(missing_ok=True)


def _baseline_metadata_path(handle: WorkspaceHandle) -> Path:
    path = handle.staging_root / f"{_BASELINE_METADATA_PREFIX}{handle.run_id}.sha256"
    if path.parent != handle.staging_root:
        raise WorkspaceError("Workspace baseline metadata path is unsafe.")
    return path


def _write_baseline_metadata(path: Path, digest: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(f"{digest}\n".encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise WorkspaceError("Unable to persist the workspace baseline digest.") from exc
    finally:
        if "descriptor" in locals() and descriptor >= 0:
            os.close(descriptor)


def _read_baseline_metadata(path: Path) -> str:
    if path.is_symlink():
        raise WorkspaceError("Workspace baseline metadata must not be a symlink.")
    try:
        content = _read_bounded_bytes(path, 65, "workspace baseline metadata")
        value = content.decode("ascii", errors="strict").strip()
    except (OSError, UnicodeDecodeError, FileToolError) as exc:
        raise WorkspaceError("Workspace baseline metadata is unavailable or invalid.") from exc
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise WorkspaceError("Workspace baseline metadata is unavailable or invalid.")
    return value


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


def normalize_allowed_files(allowed_files: Collection[str] | None) -> tuple[str, ...] | None:
    """Validate and deterministically normalize a trusted assignment-level file scope."""
    if allowed_files is None:
        return None
    normalized: set[str] = set()
    for file in allowed_files:
        if not isinstance(file, str):
            raise FileToolError("Approved file scope must contain only filenames.")
        _validate_simple_edit_file(file)
        normalized.add(file)
    if not normalized:
        raise FileToolError("Approved file scope must not be empty.")
    return tuple(sorted(normalized))


def _normalize_newlines(content: str) -> str:
    return content.replace("\r\n", "\n").replace("\r", "\n")


def _adapt_to_file_newlines(content: str, patch_text: str) -> str:
    """Adapt tool-read LF text to a consistently CRLF staged file without fuzzy matching."""
    without_crlf = content.replace("\r\n", "")
    if "\r\n" in content and "\n" not in without_crlf and "\r" not in without_crlf:
        return _normalize_newlines(patch_text).replace("\n", "\r\n")
    return patch_text


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
        if hasattr(os, "fchmod"):
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
