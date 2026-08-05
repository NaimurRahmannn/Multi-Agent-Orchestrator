from __future__ import annotations

import hashlib
import shutil
from collections.abc import Callable
from pathlib import Path

from agentorchestra.config import Settings, get_settings
from agentorchestra.exceptions import PromotionError, PromotionRollbackError
from agentorchestra.pipeline_models import PromotionResult, ResetResult
from agentorchestra.services.workspace import (
    generate_diff,
    validate_site_structure,
    validate_staged_site,
)
from agentorchestra.workspace_models import DiffReport, WorkspaceHandle

CopyTree = Callable[..., object]
RenamePath = Callable[[Path, Path], object]
RemoveTree = Callable[[Path], object]


def promote_staged_copy(
    handle: WorkspaceHandle,
    reviewed_diff: DiffReport,
    *,
    settings: Settings | None = None,
    copytree: CopyTree = shutil.copytree,
    rename_path: RenamePath | None = None,
    rmtree: RemoveTree = shutil.rmtree,
) -> PromotionResult:
    """Promote one QA-reviewed staged run to working with rollback on failure."""
    resolved_settings = settings or get_settings()
    validate_staged_site(handle)
    final_diff = generate_diff(handle, settings=resolved_settings)
    if final_diff != reviewed_diff or final_diff.is_empty:
        raise PromotionError("Reviewed staged diff changed before promotion.")

    candidate = _temporary_site_path(resolved_settings, f".agentorchestra-candidate-{handle.run_id}")
    backup = _temporary_site_path(resolved_settings, f".agentorchestra-backup-{handle.run_id}")
    _remove_if_exists(candidate, rmtree)
    _remove_if_exists(backup, rmtree)
    renamer = rename_path or _rename_path
    working = resolved_settings.working_site_dir
    candidate_cleaned = False
    backup_cleaned = False

    try:
        copytree(handle.path, candidate, symlinks=False)
        validate_site_structure(candidate)
        renamer(working, backup)
    except Exception as exc:
        _remove_if_exists(candidate, rmtree)
        raise PromotionError("Failed to prepare working-site promotion.") from exc

    try:
        renamer(candidate, working)
        validate_site_structure(working)
    except Exception as exc:
        try:
            _remove_if_exists(working, rmtree)
            renamer(backup, working)
            validate_site_structure(working)
        except Exception as rollback_exc:
            raise PromotionRollbackError(
                "Promotion failed and the working-site backup could not be restored."
            ) from rollback_exc
        _remove_if_exists(candidate, rmtree)
        raise PromotionError("Promotion failed; working-site backup was restored.") from exc

    if backup.exists():
        rmtree(backup)
    backup_cleaned = not backup.exists()
    if candidate.exists():
        rmtree(candidate)
    candidate_cleaned = not candidate.exists()
    return PromotionResult(
        run_id=handle.run_id,
        working_updated=True,
        reviewed_diff=reviewed_diff,
        final_diff=final_diff,
        candidate_cleaned=candidate_cleaned,
        backup_cleaned=backup_cleaned,
    )


def reset_working_from_fixture(
    *,
    settings: Settings | None = None,
    copytree: CopyTree = shutil.copytree,
    rename_path: RenamePath | None = None,
    rmtree: RemoveTree = shutil.rmtree,
) -> ResetResult:
    """Replace working with a fresh fixture copy using the same rollback pattern."""
    resolved_settings = settings or get_settings()
    validate_site_structure(resolved_settings.fixture_site_dir)
    validate_site_structure(resolved_settings.working_site_dir)
    candidate = _temporary_site_path(resolved_settings, ".agentorchestra-reset-candidate")
    backup = _temporary_site_path(resolved_settings, ".agentorchestra-reset-backup")
    _remove_if_exists(candidate, rmtree)
    _remove_if_exists(backup, rmtree)
    renamer = rename_path or _rename_path

    try:
        copytree(resolved_settings.fixture_site_dir, candidate, symlinks=False)
        validate_site_structure(candidate)
        renamer(resolved_settings.working_site_dir, backup)
    except Exception as exc:
        _remove_if_exists(candidate, rmtree)
        raise PromotionError("Failed to prepare demo-site reset.") from exc

    try:
        renamer(candidate, resolved_settings.working_site_dir)
        validate_site_structure(resolved_settings.working_site_dir)
    except Exception as exc:
        try:
            _remove_if_exists(resolved_settings.working_site_dir, rmtree)
            renamer(backup, resolved_settings.working_site_dir)
            validate_site_structure(resolved_settings.working_site_dir)
        except Exception as rollback_exc:
            raise PromotionRollbackError(
                "Reset failed and the working-site backup could not be restored."
            ) from rollback_exc
        _remove_if_exists(candidate, rmtree)
        raise PromotionError("Reset failed; working-site backup was restored.") from exc

    if backup.exists():
        rmtree(backup)
    if candidate.exists():
        rmtree(candidate)
    return ResetResult(
        working_reset=True,
        working_matches_fixture=_tree_digest(resolved_settings.working_site_dir)
        == _tree_digest(resolved_settings.fixture_site_dir),
        candidate_cleaned=not candidate.exists(),
        backup_cleaned=not backup.exists(),
    )


def _temporary_site_path(settings: Settings, name: str) -> Path:
    candidate = settings.working_site_dir.parent / name
    if candidate.parent != settings.working_site_dir.parent:
        raise PromotionError("Temporary promotion path is outside the site root.")
    return candidate


def _remove_if_exists(path: Path, rmtree: RemoveTree) -> None:
    if path.exists():
        if not path.is_dir() or path.is_symlink():
            raise PromotionError("Temporary promotion path is not a safe directory.")
        rmtree(path)


def _rename_path(source: Path, destination: Path) -> None:
    source.rename(destination)


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()
