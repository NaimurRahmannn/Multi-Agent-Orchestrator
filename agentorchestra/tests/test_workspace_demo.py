from agentorchestra.scripts import demo_workspace
from tests.test_workspace_service import make_settings


def test_workspace_demo_is_deterministic_and_cleans_up(tmp_path, monkeypatch, capsys):
    settings = make_settings(tmp_path)
    monkeypatch.setenv("AGENTORCHESTRA_ROOT", str(settings.project_root))

    assert demo_workspace.main() == 0
    output = capsys.readouterr().out

    assert "created staged run: workspace-demo" in output
    assert "changed files: style.css" in output
    assert "+  background: #0b3d91;" in output
    assert not (settings.staging_root_dir / "workspace-demo").exists()
