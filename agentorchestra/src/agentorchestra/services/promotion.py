from __future__ import annotations

import gc
import shutil
import time
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
from agentorchestra.services.transaction_lock import working_site_transaction
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
    """Commit a reviewed staged site under the shared working-site lock."""
    resolved = settings or get_settings()
    with working_site_transaction(resolved):
        return _promote_staged_copy_locked(
            handle,
            reviewed_diff,
            settings=resolved,
            copytree=copytree,
            rename_path=rename_path,
            rmtree=rmtree,
            workspace_cleanup=workspace_cleanup,
            temporary_id_factory=temporary_id_factory,
            digest_function=digest_function,
        )


def _promote_staged_copy_locked(
    handle: WorkspaceHandle,
    reviewed_diff: DiffReport,
    *,
    settings: Settings,
    copytree: CopyTree,
    rename_path: RenamePath | None,
    rmtree: RemoveTree,
    workspace_cleanup: WorkspaceCleanup,
    temporary_id_factory: IdFactory,
    digest_function: DigestFunction,
) -> PromotionResult:
    """Commit after the caller has serialized all working-site transactions."""
    resolved = settings
    renamer = rename_path or _rename_path
    working = resolved.working_site_dir

    validate_site_structure(resolved.fixture_site_dir)
    validate_site_structure(working)
    pre_working = digest_function(working)
    if handle.source_working_digest is None:
        raise PromotionError("Staged workspace is missing its working-site baseline digest.")
    if pre_working.digest != handle.source_working_digest:
        raise PromotionError("Working site changed since the staged workspace was created.")
    validate_staged_site(handle)

    final_diff = generate_diff(handle, settings=resolved)
    if final_diff != reviewed_diff or final_diff.is_empty:
        raise PromotionError("Reviewed staged diff changed before promotion.")

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
        _copytree_with_transient_retry(handle.path, candidate, copytree, rmtree)
        validate_site_structure(candidate)
        candidate_digest = digest_function(candidate)
        if candidate_digest != staged:
            raise PromotionError("Promotion candidate does not match reviewed staging content.")
    except Exception as exc:
        _best_effort_remove(candidate, rmtree)
        if isinstance(exc, PromotionError):
            raise
        raise PromotionError(
            f"Failed to prepare working-site promotion ({exc.__class__.__name__})."
        ) from exc

    old_working_moved = False
    gc.collect()
    try:
        _rename_with_transient_retry(
            working,
            backup,
            renamer,
            retry_permission=rename_path is None,
        )
        old_working_moved = True
        _rename_with_transient_retry(
            candidate,
            working,
            renamer,
            retry_permission=rename_path is None,
        )
        validate_site_structure(working)
        final_working = digest_function(working)
        if final_working != staged:
            raise PromotionError("Installed working site does not match reviewed staging content.")
    except Exception as exc:
        if not old_working_moved:
            _best_effort_remove(candidate, rmtree)
            raise PromotionError(
                f"Failed to prepare working-site promotion ({exc.__class__.__name__})."
            ) from exc
        _restore_working_or_raise(
            working=working,
            backup=backup,
            candidate=candidate,
            expected=pre_working,
            renamer=renamer,
            rmtree=rmtree,
            digest_function=digest_function,
            operation="Promotion",
            retry_permission=rename_path is None,
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
    status = PromotionStatus.COMMITTED_WITH_WARNING if warnings else PromotionStatus.COMMITTED
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
    """Transactionally replace working under the shared working-site lock."""
    resolved = settings or get_settings()
    with working_site_transaction(resolved):
        return _reset_working_from_fixture_locked(
            settings=resolved,
            copytree=copytree,
            rename_path=rename_path,
            rmtree=rmtree,
            temporary_id_factory=temporary_id_factory,
            digest_function=digest_function,
        )


def _reset_working_from_fixture_locked(
    *,
    settings: Settings,
    copytree: CopyTree,
    rename_path: RenamePath | None,
    rmtree: RemoveTree,
    temporary_id_factory: IdFactory,
    digest_function: DigestFunction,
) -> ResetResult:
    """Reset after the caller has serialized all working-site transactions."""
    resolved = settings
    renamer = rename_path or _rename_path
    fixture = resolved.fixture_site_dir
    working = resolved.working_site_dir
    validate_site_structure(fixture)
    validate_site_structure(working)
    fixture_digest = digest_function(fixture)
    pre_working = digest_function(working)

    transaction_id = _safe_generated_id(temporary_id_factory())
    candidate = _temporary_site_path(resolved, f".agentorchestra-reset-candidate-{transaction_id}")
    backup = _temporary_site_path(resolved, f".agentorchestra-reset-backup-{transaction_id}")
    _require_available(candidate)
    _require_available(backup)

    try:
        _copytree_with_transient_retry(fixture, candidate, copytree, rmtree)
        validate_site_structure(candidate)
        if digest_function(candidate) != fixture_digest:
            raise PromotionError("Reset candidate does not match fixture content.")
    except Exception as exc:
        _best_effort_remove(candidate, rmtree)
        if isinstance(exc, PromotionError):
            raise
        raise PromotionError("Failed to prepare demo-site reset.") from exc

    old_working_moved = False
    gc.collect()
    try:
        _rename_with_transient_retry(
            working,
            backup,
            renamer,
            retry_permission=rename_path is None,
        )
        old_working_moved = True
        _rename_with_transient_retry(
            candidate,
            working,
            renamer,
            retry_permission=rename_path is None,
        )
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
            retry_permission=rename_path is None,
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
    status = PromotionStatus.COMMITTED_WITH_WARNING if warnings else PromotionStatus.COMMITTED
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
    retry_permission: bool,
) -> None:
    try:
        if working.exists() or working.is_symlink():
            _remove_managed_directory(working, rmtree)
        if not backup.is_dir() or backup.is_symlink():
            raise PromotionError("Working-site backup is unavailable for restoration.")
        _rename_with_transient_retry(
            backup,
            working,
            renamer,
            retry_permission=retry_permission,
        )
        validate_site_structure(working)
        if digest_function(working) != expected:
            raise PromotionError("Restored working site does not match its original content.")
    except Exception as rollback_exc:
        raise PromotionRollbackError(
            f"{operation} failed and the original working site could not be restored safely.",
            recovery_paths=_recovery_identifiers(working, backup, candidate),
        ) from rollback_exc


def _copytree_with_transient_retry(
    source: Path,
    destination: Path,
    copytree: CopyTree,
    rmtree: RemoveTree,
) -> object:
    """Retry short-lived Windows file locks without weakening transaction checks."""
    for attempt in range(3):
        try:
            return copytree(source, destination, symlinks=False)
        except PermissionError:
            if (destination.exists() or destination.is_symlink()) and not _best_effort_remove(
                destination, rmtree
            ):
                raise
            if attempt == 2:
                raise
            time.sleep(0.02 * (attempt + 1))
    raise AssertionError("unreachable")


def _rename_with_transient_retry(
    source: Path,
    destination: Path,
    renamer: RenamePath,
    *,
    retry_permission: bool,
) -> object:
    for attempt in range(5):
        try:
            return renamer(source, destination)
        except PermissionError:
            if not source.exists() and destination.exists():
                return None
            if (
                not retry_permission
                or not source.exists()
                or destination.exists()
                or attempt == 4
            ):
                raise
            gc.collect()
            time.sleep(0.05 * (attempt + 1))
    raise AssertionError("unreachable")


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
