from agentorchestra.exceptions import PromotionRollbackError
from agentorchestra.pipeline_models import ResetResult
from agentorchestra.scripts import reset_demo_site
from tests.test_workspace_service import make_settings


def test_reset_cli_requires_confirmation(tmp_path, capsys):
    settings = make_settings(tmp_path)

    code = reset_demo_site.main([], settings=settings)
    output = capsys.readouterr().out

    assert code == 2
    assert "--reset" in output


def test_reset_cli_restores_working_from_fixture(tmp_path, capsys):
    settings = make_settings(tmp_path)
    (settings.working_site_dir / "style.css").write_text("body { color: red; }\n", encoding="utf-8")

    code = reset_demo_site.main(["--reset"], settings=settings)
    output = capsys.readouterr().out

    assert code == 0
    assert "reset status: succeeded" in output
    assert "working matches fixture: yes" in output
    assert (settings.working_site_dir / "style.css").read_text(encoding="utf-8") == (
        settings.fixture_site_dir / "style.css"
    ).read_text(encoding="utf-8")


def test_reset_cli_reports_committed_cleanup_warning(monkeypatch, tmp_path, capsys):
    settings = make_settings(tmp_path)
    warning = "Could not remove obsolete reset backup '.agentorchestra-reset-backup-test'."
    result = ResetResult(
        status="committed_with_warning",
        working_reset=True,
        working_matches_fixture=True,
        candidate_cleaned=True,
        backup_cleaned=False,
        fixture_digest="a" * 64,
        final_working_digest="a" * 64,
        warnings=[warning],
        message="Reset with warning.",
    )
    monkeypatch.setattr(reset_demo_site, "reset_working_from_fixture", lambda **_kwargs: result)

    code = reset_demo_site.main(["--reset"], settings=settings)
    output = capsys.readouterr().out

    assert code == 0
    assert "reset transaction: committed_with_warning" in output
    assert "cleanup warning:" in output


def test_reset_cli_uses_critical_recovery_exit_code(monkeypatch, tmp_path, capsys):
    settings = make_settings(tmp_path)

    def fail_reset(**_kwargs):
        raise PromotionRollbackError(
            "Recovery required.",
            recovery_paths=("working", ".agentorchestra-reset-backup-test"),
        )

    monkeypatch.setattr(reset_demo_site, "reset_working_from_fixture", fail_reset)

    code = reset_demo_site.main(["--reset"], settings=settings)
    output = capsys.readouterr().out

    assert code == reset_demo_site.CRITICAL_RECOVERY_EXIT_CODE == 9
    assert "Critical: working-site recovery is required" in output
    assert str(tmp_path) not in output
