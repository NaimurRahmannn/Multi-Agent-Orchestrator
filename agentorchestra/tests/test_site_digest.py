import shutil

from agentorchestra.services.site_digest import compute_site_tree_digest
from tests.test_workspace_service import make_settings


def test_site_tree_digest_is_deterministic_and_portable(tmp_path):
    settings = make_settings(tmp_path)

    first = compute_site_tree_digest(settings.working_site_dir)
    second = compute_site_tree_digest(settings.working_site_dir)

    assert first == second
    assert first.files == sorted(first.files)
    assert all("\\" not in file and not file.startswith("/") for file in first.files)
    assert str(tmp_path) not in first.model_dump_json()


def test_site_tree_digest_includes_names_and_exact_file_bytes(tmp_path):
    settings = make_settings(tmp_path)
    original = compute_site_tree_digest(settings.working_site_dir)

    (settings.working_site_dir / "index.html").write_text(
        (settings.working_site_dir / "index.html").read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    assert compute_site_tree_digest(settings.working_site_dir).digest != original.digest

    shutil.rmtree(settings.working_site_dir)
    shutil.copytree(settings.fixture_site_dir, settings.working_site_dir)
    (settings.working_site_dir / "style.css").write_text(
        (settings.working_site_dir / "style.css").read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    assert compute_site_tree_digest(settings.working_site_dir).digest != original.digest

    shutil.rmtree(settings.working_site_dir)
    shutil.copytree(settings.fixture_site_dir, settings.working_site_dir)
    asset = settings.working_site_dir / "assets" / "studio-mark.svg"
    asset.write_bytes(asset.read_bytes() + b"\n")
    assert compute_site_tree_digest(settings.working_site_dir).digest != original.digest


def test_filename_participates_in_site_tree_digest(tmp_path):
    settings = make_settings(tmp_path)
    original = compute_site_tree_digest(settings.working_site_dir)
    asset = settings.working_site_dir / "assets" / "studio-mark.svg"
    asset.rename(settings.working_site_dir / "assets" / "renamed-mark.svg")

    renamed = compute_site_tree_digest(settings.working_site_dir)

    assert renamed.digest != original.digest
    assert renamed.total_bytes == original.total_bytes
