from types import SimpleNamespace

from agentorchestra.scripts import run_ui
from tests.test_workspace_service import make_settings


def test_ui_launcher_uses_fixed_path_argument_list_and_shell_false(tmp_path):
    settings = make_settings(tmp_path)
    app = settings.source_dir / "agentorchestra" / "ui" / "app.py"
    app.parent.mkdir(parents=True)
    app.write_text("print('ui')\n", encoding="utf-8")
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    assert run_ui.main([], settings=settings, subprocess_runner=runner) == 0
    command, kwargs = calls[0]
    assert command[1:4] == ["-m", "streamlit", "run"]
    assert command[4] == str(app)
    assert kwargs["shell"] is False
    assert kwargs["cwd"] == settings.project_root


def test_ui_launcher_help_starts_no_subprocess(tmp_path):
    settings = make_settings(tmp_path)
    calls = []
    try:
        run_ui.main(["--help"], settings=settings, subprocess_runner=lambda *a, **k: calls.append(1))
    except SystemExit as exc:
        assert exc.code == 0
    assert calls == []
