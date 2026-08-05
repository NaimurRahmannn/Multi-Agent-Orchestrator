import os
import shutil

import pytest

from agentorchestra.exceptions import PromotionError, PromotionRollbackError
from agentorchestra.models import SpecialistName
from agentorchestra.services.promotion import promote_staged_copy, reset_working_from_fixture
from agentorchestra.services.site_digest import compute_site_tree_digest
from agentorchestra.services.workspace import create_staged_copy, generate_diff, propose_patch
from tests.test_workspace_service import make_settings


def test_promote_staged_copy_replaces_working_and_cleans_temps(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "promote")
    propose_patch(
        handle,
        specialist=SpecialistName.CSS,
        file="style.css",
        old_text="  background: var(--accent);\n",
        new_text="  background: #0b3d91;\n",
        summary="Promote CSS edit.",
    )
    reviewed = generate_diff(handle, settings=settings)

    result = promote_staged_copy(handle, reviewed, settings=settings)

    assert result.working_updated is True
    assert result.reviewed_diff == result.final_diff == reviewed
    assert "background: #0b3d91" in (settings.working_site_dir / "style.css").read_text()
    assert (settings.fixture_site_dir / "style.css").read_text() != (
        settings.working_site_dir / "style.css"
    ).read_text()
    assert result.candidate_cleaned is True
    assert result.backup_cleaned is True
    assert result.staging_cleaned is True
    assert result.status == "committed"
    assert result.accepted_content_digest == result.final_working_digest
    assert not handle.path.exists()


def test_promote_rejects_staging_mutation_after_qa_review(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "changed")
    propose_patch(
        handle,
        specialist=SpecialistName.CSS,
        file="style.css",
        old_text="  background: var(--accent);\n",
        new_text="  background: #0b3d91;\n",
        summary="Promote CSS edit.",
    )
    reviewed = generate_diff(handle, settings=settings)
    (handle.path / "style.css").write_text("body { color: red; }\n", encoding="utf-8")

    with pytest.raises(PromotionError):
        promote_staged_copy(handle, reviewed, settings=settings)

    assert "var(--accent)" in (settings.working_site_dir / "style.css").read_text()


def test_promotion_failure_restores_working_backup(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "restore")
    before = (settings.working_site_dir / "style.css").read_bytes()
    propose_patch(
        handle,
        specialist=SpecialistName.CSS,
        file="style.css",
        old_text="  background: var(--accent);\n",
        new_text="  background: #0b3d91;\n",
        summary="Promote CSS edit.",
    )
    reviewed = generate_diff(handle, settings=settings)
    calls = 0

    def fail_second_rename(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated candidate failure")
        os.rename(source, destination)

    with pytest.raises(PromotionError):
        promote_staged_copy(handle, reviewed, settings=settings, rename_path=fail_second_rename)

    assert (settings.working_site_dir / "style.css").read_bytes() == before


def test_candidate_digest_mismatch_prevents_commit(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "candidate-mismatch")
    propose_patch(
        handle,
        specialist=SpecialistName.CSS,
        file="style.css",
        old_text="  background: var(--accent);\n",
        new_text="  background: #0b3d91;\n",
        summary="Promote CSS edit.",
    )
    reviewed = generate_diff(handle, settings=settings)
    before = compute_site_tree_digest(settings.working_site_dir)

    def tampered_copy(source, destination, **kwargs):
        shutil.copytree(source, destination, **kwargs)
        (destination / "style.css").write_text("body { color: red; }\n", encoding="utf-8")

    with pytest.raises(PromotionError, match="candidate does not match"):
        promote_staged_copy(
            handle,
            reviewed,
            settings=settings,
            copytree=tampered_copy,
            temporary_id_factory=lambda: "tx",
        )

    assert compute_site_tree_digest(settings.working_site_dir) == before
    assert not any("candidate" in path.name for path in settings.working_site_dir.parent.iterdir())


def test_final_working_digest_mismatch_rolls_back_exact_original(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "final-mismatch")
    propose_patch(
        handle,
        specialist=SpecialistName.CSS,
        file="style.css",
        old_text="  background: var(--accent);\n",
        new_text="  background: #0b3d91;\n",
        summary="Promote CSS edit.",
    )
    reviewed = generate_diff(handle, settings=settings)
    before = compute_site_tree_digest(settings.working_site_dir)
    working_calls = 0

    def mismatching_final_digest(path):
        nonlocal working_calls
        actual = compute_site_tree_digest(path)
        if path == settings.working_site_dir:
            working_calls += 1
            if working_calls == 2:
                return actual.model_copy(update={"digest": "f" * 64})
        return actual

    with pytest.raises(PromotionError) as error:
        promote_staged_copy(
            handle,
            reviewed,
            settings=settings,
            digest_function=mismatching_final_digest,
        )

    assert error.value.working_restored is True
    assert compute_site_tree_digest(settings.working_site_dir) == before


def test_rollback_failure_is_critical_and_preserves_recovery_material(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "critical")
    propose_patch(
        handle,
        specialist=SpecialistName.CSS,
        file="style.css",
        old_text="  background: var(--accent);\n",
        new_text="  background: #0b3d91;\n",
        summary="Promote CSS edit.",
    )
    reviewed = generate_diff(handle, settings=settings)
    calls = 0

    def fail_install_and_restore(source, destination):
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise OSError("simulated rename failure")
        os.rename(source, destination)

    with pytest.raises(PromotionRollbackError) as error:
        promote_staged_copy(
            handle,
            reviewed,
            settings=settings,
            rename_path=fail_install_and_restore,
            temporary_id_factory=lambda: "recovery",
        )

    assert error.value.recovery_paths
    assert any("backup" in path.name for path in settings.working_site_dir.parent.iterdir())
    assert handle.path.exists()


def test_post_commit_backup_cleanup_failure_is_success_with_warning(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "backup-warning")
    propose_patch(
        handle,
        specialist=SpecialistName.CSS,
        file="style.css",
        old_text="  background: var(--accent);\n",
        new_text="  background: #0b3d91;\n",
        summary="Promote CSS edit.",
    )
    reviewed = generate_diff(handle, settings=settings)

    def fail_backup_cleanup(path):
        if "backup" in path.name:
            raise OSError("simulated cleanup failure")
        shutil.rmtree(path)

    result = promote_staged_copy(
        handle,
        reviewed,
        settings=settings,
        rmtree=fail_backup_cleanup,
        temporary_id_factory=lambda: "warning",
    )

    assert result.status == "committed_with_warning"
    assert result.working_updated is True
    assert result.backup_cleaned is False
    assert result.staging_cleaned is True
    assert result.warnings
    assert result.accepted_content_digest == result.final_working_digest


def test_post_commit_staging_cleanup_failure_is_success_with_warning(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "staging-warning")
    propose_patch(
        handle,
        specialist=SpecialistName.CSS,
        file="style.css",
        old_text="  background: var(--accent);\n",
        new_text="  background: #0b3d91;\n",
        summary="Promote CSS edit.",
    )
    reviewed = generate_diff(handle, settings=settings)

    result = promote_staged_copy(
        handle,
        reviewed,
        settings=settings,
        workspace_cleanup=lambda _handle: (_ for _ in ()).throw(OSError("cleanup")),
    )

    assert result.status == "committed_with_warning"
    assert result.working_updated is True
    assert result.staging_cleaned is False
    assert handle.path.exists()


def test_reset_working_from_fixture_restores_original_site(tmp_path):
    settings = make_settings(tmp_path)
    (settings.working_site_dir / "style.css").write_text("body { color: red; }\n", encoding="utf-8")

    result = reset_working_from_fixture(settings=settings)

    assert result.working_reset is True
    assert result.working_matches_fixture is True
    assert result.candidate_cleaned is True
    assert result.backup_cleaned is True
    assert (settings.working_site_dir / "style.css").read_text() == (
        settings.fixture_site_dir / "style.css"
    ).read_text()
    assert result.status == "committed"
    assert result.fixture_digest == result.final_working_digest


def test_reset_candidate_mismatch_leaves_working_untouched(tmp_path):
    settings = make_settings(tmp_path)
    (settings.working_site_dir / "style.css").write_text("body { color: red; }\n", encoding="utf-8")
    before = compute_site_tree_digest(settings.working_site_dir)

    def tampered_copy(source, destination, **kwargs):
        shutil.copytree(source, destination, **kwargs)
        (destination / "style.css").write_text("body { color: blue; }\n", encoding="utf-8")

    with pytest.raises(PromotionError, match="candidate does not match"):
        reset_working_from_fixture(
            settings=settings,
            copytree=tampered_copy,
            temporary_id_factory=lambda: "candidate-mismatch",
        )

    assert compute_site_tree_digest(settings.working_site_dir) == before


def test_reset_post_install_mismatch_restores_original_digest(tmp_path):
    settings = make_settings(tmp_path)
    (settings.working_site_dir / "style.css").write_text("body { color: red; }\n", encoding="utf-8")
    before = compute_site_tree_digest(settings.working_site_dir)
    working_calls = 0

    def mismatching_final_digest(path):
        nonlocal working_calls
        actual = compute_site_tree_digest(path)
        if path == settings.working_site_dir:
            working_calls += 1
            if working_calls == 2:
                return actual.model_copy(update={"digest": "e" * 64})
        return actual

    with pytest.raises(PromotionError) as error:
        reset_working_from_fixture(
            settings=settings,
            digest_function=mismatching_final_digest,
        )

    assert error.value.working_restored is True
    assert compute_site_tree_digest(settings.working_site_dir) == before


def test_reset_backup_cleanup_failure_is_success_with_warning(tmp_path):
    settings = make_settings(tmp_path)
    (settings.working_site_dir / "style.css").write_text("body { color: red; }\n", encoding="utf-8")

    def fail_backup_cleanup(path):
        if "backup" in path.name:
            raise OSError("simulated cleanup failure")
        shutil.rmtree(path)

    result = reset_working_from_fixture(
        settings=settings,
        rmtree=fail_backup_cleanup,
        temporary_id_factory=lambda: "warning",
    )

    assert result.status == "committed_with_warning"
    assert result.working_reset is True
    assert result.working_matches_fixture is True
    assert result.backup_cleaned is False
    assert result.warnings


def test_reset_rollback_failure_is_critical_and_keeps_unrelated_staging(tmp_path):
    settings = make_settings(tmp_path)
    (settings.working_site_dir / "style.css").write_text("body { color: red; }\n", encoding="utf-8")
    unrelated = settings.staging_root_dir / "unrelated"
    unrelated.mkdir(parents=True)
    (unrelated / "keep.txt").write_text("keep", encoding="utf-8")
    calls = 0

    def fail_install_and_restore(source, destination):
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise OSError("simulated rename failure")
        os.rename(source, destination)

    with pytest.raises(PromotionRollbackError) as error:
        reset_working_from_fixture(
            settings=settings,
            rename_path=fail_install_and_restore,
            temporary_id_factory=lambda: "recovery",
        )

    assert error.value.recovery_paths
    assert unrelated.exists()
    assert any("backup" in path.name for path in settings.working_site_dir.parent.iterdir())
