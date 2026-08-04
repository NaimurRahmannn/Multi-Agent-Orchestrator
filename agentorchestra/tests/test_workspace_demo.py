from agentorchestra.scripts import demo_workspace
from tests.test_workspace_service import make_settings


def test_workspace_demo_is_deterministic_and_cleans_up(tmp_path, monkeypatch, capsys):
    settings = make_settings(tmp_path)
    monkeypatch.setenv("AGENTORCHESTRA_ROOT", str(settings.project_root))
    working_before = (settings.working_site_dir / "style.css").read_bytes()
    fixture_before = (settings.fixture_site_dir / "style.css").read_bytes()

    assert demo_workspace.main() == 0
    output = capsys.readouterr().out

    assert "created staged run: workspace-demo" in output
    assert "patch status: applied" in output
    assert "patched file: style.css" in output
    assert "match count: 1" in output
    assert "changed files: style.css" in output
    assert "added lines: 1" in output
    assert "removed lines: 1" in output
    assert "working unchanged: yes" in output
    assert "fixture unchanged: yes" in output
    assert "staging cleanup: complete" in output
    assert "+  background: #0b3d91;" in output
    assert not (settings.staging_root_dir / "workspace-demo").exists()
    assert (settings.working_site_dir / "style.css").read_bytes() == working_before
    assert (settings.fixture_site_dir / "style.css").read_bytes() == fixture_before
