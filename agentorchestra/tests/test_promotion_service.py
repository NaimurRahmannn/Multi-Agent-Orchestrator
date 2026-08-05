import os

import pytest

from agentorchestra.exceptions import PromotionError
from agentorchestra.models import SpecialistName
from agentorchestra.services.promotion import promote_staged_copy, reset_working_from_fixture
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
