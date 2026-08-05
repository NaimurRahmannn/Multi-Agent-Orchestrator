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
