from __future__ import annotations

import shutil
import uuid
from collections.abc import Callable
from pathlib import Path

from agentorchestra.config import Settings, get_settings
from agentorchestra.exceptions import PromotionError, PromotionRollbackError
from agentorchestra.pipeline_models import (
    PromotionResult,
    PromotionStatus,
    ResetResult,
    SiteTreeDigest,
)
from agentorchestra.services.site_digest import compute_site_tree_digest
from agentorchestra.services.workspace import (
    cleanup_staged_workspace,
    generate_diff,
    validate_site_structure,
    validate_staged_site,
)
from agentorchestra.workspace_models import DiffReport, WorkspaceHandle

CopyTree = Callable[..., object]
RenamePath = Callable[[Path, Path], object]
RemoveTree = Callable[[Path], object]
WorkspaceCleanup = Callable[[WorkspaceHandle], None]
IdFactory = Callable[[], str]
DigestFunction = Callable[[Path], SiteTreeDigest]


def promote_staged_copy(
    handle: WorkspaceHandle,
    reviewed_diff: DiffReport,
    *,
    settings: Settings | None = None,
    copytree: CopyTree = shutil.copytree,
    rename_path: RenamePath | None = None,
    rmtree: RemoveTree = shutil.rmtree,
    workspace_cleanup: WorkspaceCleanup = cleanup_staged_workspace,
    temporary_id_factory: IdFactory = lambda: uuid.uuid4().hex,
    digest_function: DigestFunction = compute_site_tree_digest,
) -> PromotionResult:
    """Commit a reviewed staged site, rolling back only commit/validation failures."""
    resolved = settings or get_settings()
    renamer = rename_path or _rename_path
    working = resolved.working_site_dir

    validate_site_structure(resolved.fixture_site_dir)
    validate_site_structure(working)
    validate_staged_site(handle)
    final_diff = generate_diff(handle, settings=resolved)
    if final_diff != reviewed_diff or final_diff.is_empty:
        raise PromotionError("Reviewed staged diff changed before promotion.")

    pre_working = digest_function(working)
    staged = digest_function(handle.path)
    transaction_id = _safe_generated_id(temporary_id_factory())
    candidate = _temporary_site_path(
        resolved, f".agentorchestra-candidate-{handle.run_id}-{transaction_id}"
    )
    backup = _temporary_site_path(
        resolved, f".agentorchestra-backup-{handle.run_id}-{transaction_id}"
    )
    _require_available(candidate)
    _require_available(backup)

    try:
        copytree(handle.path, candidate, symlinks=False)
        validate_site_structure(candidate)
        candidate_digest = digest_function(candidate)
        if candidate_digest != staged:
            raise PromotionError("Promotion candidate does not match reviewed staging content.")
    except Exception as exc:
        _best_effort_remove(candidate, rmtree)
        if isinstance(exc, PromotionError):
            raise
        raise PromotionError("Failed to prepare working-site promotion.") from exc

    old_working_moved = False
    try:
        renamer(working, backup)
        old_working_moved = True
        renamer(candidate, working)
        validate_site_structure(working)
        final_working = digest_function(working)
        if final_working != staged:
            raise PromotionError("Installed working site does not match reviewed staging content.")
    except Exception as exc:
        if not old_working_moved:
            _best_effort_remove(candidate, rmtree)
            raise PromotionError("Failed to prepare working-site promotion.") from exc
        _restore_working_or_raise(
            working=working,
            backup=backup,
            candidate=candidate,
            expected=pre_working,
            renamer=renamer,
            rmtree=rmtree,
            digest_function=digest_function,
            operation="Promotion",
        )
        _best_effort_remove(candidate, rmtree)
        raise PromotionError(
            "Promotion failed; the original working site was restored and verified.",
            working_restored=True,
        ) from exc

    warnings: list[str] = []
    backup_cleaned = _cleanup_after_commit(
        backup,
        rmtree,
        warnings,
        "obsolete promotion backup",
    )
    staging_cleaned = _cleanup_workspace_after_commit(handle, workspace_cleanup, warnings)
    candidate_cleaned = _cleanup_after_commit(
        candidate,
        rmtree,
        warnings,
        "promotion candidate",
    )
    status = (
        PromotionStatus.COMMITTED_WITH_WARNING if warnings else PromotionStatus.COMMITTED
    )
    return PromotionResult(
        run_id=handle.run_id,
        status=status,
        working_updated=True,
        working_restored=False,
        reviewed_diff=reviewed_diff,
        final_diff=final_diff,
        staging_cleaned=staging_cleaned,
        candidate_cleaned=candidate_cleaned,
        backup_cleaned=backup_cleaned,
        accepted_content_digest=staged.digest,
        final_working_digest=final_working.digest,
        warnings=warnings,
        message=(
            "Reviewed content committed with cleanup warnings."
            if warnings
            else "Reviewed content committed and temporary paths cleaned."
        ),
        recovery_required=False,
    )


def reset_working_from_fixture(
    *,
    settings: Settings | None = None,
    copytree: CopyTree = shutil.copytree,
    rename_path: RenamePath | None = None,
    rmtree: RemoveTree = shutil.rmtree,
    temporary_id_factory: IdFactory = lambda: uuid.uuid4().hex,
    digest_function: DigestFunction = compute_site_tree_digest,
) -> ResetResult:
    """Transactionally replace working with an exact fixture copy."""
    resolved = settings or get_settings()
    renamer = rename_path or _rename_path
    fixture = resolved.fixture_site_dir
    working = resolved.working_site_dir
    validate_site_structure(fixture)
    validate_site_structure(working)
    fixture_digest = digest_function(fixture)
    pre_working = digest_function(working)

    transaction_id = _safe_generated_id(temporary_id_factory())
    candidate = _temporary_site_path(
        resolved, f".agentorchestra-reset-candidate-{transaction_id}"
    )
    backup = _temporary_site_path(resolved, f".agentorchestra-reset-backup-{transaction_id}")
    _require_available(candidate)
    _require_available(backup)

    try:
        copytree(fixture, candidate, symlinks=False)
        validate_site_structure(candidate)
        if digest_function(candidate) != fixture_digest:
            raise PromotionError("Reset candidate does not match fixture content.")
    except Exception as exc:
        _best_effort_remove(candidate, rmtree)
        if isinstance(exc, PromotionError):
            raise
        raise PromotionError("Failed to prepare demo-site reset.") from exc

    old_working_moved = False
    try:
        renamer(working, backup)
        old_working_moved = True
        renamer(candidate, working)
        validate_site_structure(working)
        final_working = digest_function(working)
        if final_working != fixture_digest:
            raise PromotionError("Installed reset site does not match fixture content.")
    except Exception as exc:
        if not old_working_moved:
            _best_effort_remove(candidate, rmtree)
            raise PromotionError("Failed to prepare demo-site reset.") from exc
        _restore_working_or_raise(
            working=working,
            backup=backup,
            candidate=candidate,
            expected=pre_working,
            renamer=renamer,
            rmtree=rmtree,
            digest_function=digest_function,
            operation="Reset",
        )
        _best_effort_remove(candidate, rmtree)
        raise PromotionError(
            "Reset failed; the original working site was restored and verified.",
            working_restored=True,
        ) from exc

    warnings: list[str] = []
    backup_cleaned = _cleanup_after_commit(
        backup,
        rmtree,
        warnings,
        "obsolete reset backup",
    )
    candidate_cleaned = _cleanup_after_commit(
        candidate,
        rmtree,
        warnings,
        "reset candidate",
    )
    status = (
        PromotionStatus.COMMITTED_WITH_WARNING if warnings else PromotionStatus.COMMITTED
    )
    return ResetResult(
        status=status,
        working_reset=True,
        working_matches_fixture=True,
        working_restored=False,
        candidate_cleaned=candidate_cleaned,
        backup_cleaned=backup_cleaned,
        fixture_digest=fixture_digest.digest,
        final_working_digest=final_working.digest,
        warnings=warnings,
        message=(
            "Working site reset with cleanup warnings."
            if warnings
            else "Working site reset to the fixture and temporary paths cleaned."
        ),
        recovery_required=False,
    )


def _restore_working_or_raise(
    *,
    working: Path,
    backup: Path,
    candidate: Path,
    expected: SiteTreeDigest,
    renamer: RenamePath,
    rmtree: RemoveTree,
    digest_function: DigestFunction,
    operation: str,
) -> None:
    try:
        if working.exists() or working.is_symlink():
            _remove_managed_directory(working, rmtree)
        if not backup.is_dir() or backup.is_symlink():
            raise PromotionError("Working-site backup is unavailable for restoration.")
        renamer(backup, working)
        validate_site_structure(working)
        if digest_function(working) != expected:
            raise PromotionError("Restored working site does not match its original content.")
    except Exception as rollback_exc:
        raise PromotionRollbackError(
            f"{operation} failed and the original working site could not be restored safely.",
            recovery_paths=_recovery_identifiers(working, backup, candidate),
        ) from rollback_exc


def _temporary_site_path(settings: Settings, name: str) -> Path:
    site_parent = settings.working_site_dir.parent
    path = site_parent / name
    if path.parent != site_parent or not name.startswith(".agentorchestra-"):
        raise PromotionError("Temporary transaction path is outside the site root.")
    return path


def _safe_generated_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 80
        or not all(character.isalnum() or character in {"-", "_"} for character in value)
    ):
        raise PromotionError("Generated transaction identifier is invalid.")
    return value


def _require_available(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise PromotionError("Generated temporary transaction path already exists.")


def _remove_managed_directory(path: Path, rmtree: RemoveTree) -> None:
    if path.is_symlink() or not path.is_dir():
        raise PromotionError("Transaction path is not a safe directory.")
    rmtree(path)


def _best_effort_remove(path: Path, rmtree: RemoveTree) -> bool:
    if not path.exists() and not path.is_symlink():
        return True
    try:
        _remove_managed_directory(path, rmtree)
    except Exception:
        return False
    return not path.exists() and not path.is_symlink()


def _cleanup_after_commit(
    path: Path,
    rmtree: RemoveTree,
    warnings: list[str],
    label: str,
) -> bool:
    cleaned = _best_effort_remove(path, rmtree)
    if not cleaned:
        warnings.append(f"Could not remove {label} '{path.name}'.")
    return cleaned


def _cleanup_workspace_after_commit(
    handle: WorkspaceHandle,
    cleanup: WorkspaceCleanup,
    warnings: list[str],
) -> bool:
    try:
        cleanup(handle)
    except Exception:
        warnings.append(f"Could not remove staged run '{handle.run_id}'.")
        return False
    cleaned = not handle.path.exists() and not handle.path.is_symlink()
    if not cleaned:
        warnings.append(f"Could not remove staged run '{handle.run_id}'.")
    return cleaned


def _recovery_identifiers(working: Path, backup: Path, candidate: Path) -> tuple[str, ...]:
    names = [
        name
        for path, name in (
            (working, "working"),
            (backup, backup.name),
            (candidate, candidate.name),
        )
        if path.exists() or path.is_symlink()
    ]
    return tuple(names)


def _rename_path(source: Path, destination: Path) -> None:
    source.rename(destination)
