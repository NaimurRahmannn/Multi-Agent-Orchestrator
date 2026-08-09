from pathlib import Path
from types import SimpleNamespace

from agentorchestra.scripts import verify_clean_install

ROOT = Path(__file__).resolve().parents[1]


def test_source_copy_excludes_secrets_environments_dependencies_and_artifacts(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for name in (".env",):
        (source / name).write_text("secret", encoding="utf-8")
    for name in (".venv", "node_modules", ".git"):
        (source / name).mkdir()
        (source / name / "private.txt").write_text("private", encoding="utf-8")
    (source / "reports" / "lighthouse").mkdir(parents=True)
    (source / "reports" / "lighthouse" / "report.json").write_text("{}", encoding="utf-8")
    (source / "sites" / "staging").mkdir(parents=True)
    (source / "sites" / "staging" / "active.txt").write_text("active", encoding="utf-8")
    (source / "README.md").write_text("safe", encoding="utf-8")

    destination = tmp_path / "copy"
    verify_clean_install.copy_source(source, destination)

    assert (destination / "README.md").is_file()
    assert not (destination / ".env").exists()
    assert not (destination / ".venv").exists()
    assert not (destination / "node_modules").exists()
    assert not (destination / ".git").exists()
    assert not (destination / "reports" / "lighthouse").exists()
    assert not (destination / "sites" / "staging" / "active.txt").exists()


def test_offline_checks_validate_current_repository_without_network():
    checks = verify_clean_install.offline_checks(ROOT)
    assert checks
    assert all(passed for _, passed, _ in checks), checks


def test_offline_checks_reject_working_tree_that_differs_from_fixture(tmp_path):
    root = tmp_path / "repository"
    fixture = root / "sites" / "fixture"
    working = root / "sites" / "working"
    fixture.mkdir(parents=True)
    working.mkdir(parents=True)
    (root / ".gitignore").write_text(
        ".env\nnode_modules/\nreports/lighthouse/*\nsites/staging/*\n",
        encoding="utf-8",
    )
    for name in ("index.html", "about.html", "contact.html", "style.css"):
        (fixture / name).write_text(f"fixture {name}", encoding="utf-8")
        (working / name).write_text(f"fixture {name}", encoding="utf-8")

    matching = {name: passed for name, passed, _ in verify_clean_install.offline_checks(root)}
    assert matching["sample-site baseline"] is True

    (working / "index.html").write_text("committed drift", encoding="utf-8")
    mismatched = {name: passed for name, passed, _ in verify_clean_install.offline_checks(root)}
    assert mismatched["sample-site baseline"] is False


def test_full_mode_requires_apply_before_copy_or_subprocess(tmp_path):
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        raise AssertionError("subprocess must not run")

    assert verify_clean_install.main(
        ["--full"], source_root=tmp_path, runner=runner
    ) == 2
    assert calls == []


def test_full_runner_normalizes_only_disposable_working_and_uses_argument_lists(tmp_path):
    root = tmp_path / "copy"
    fixture = root / "sites" / "fixture"
    working = root / "sites" / "working"
    fixture.mkdir(parents=True)
    working.mkdir(parents=True)
    (fixture / "index.html").write_text("fixture", encoding="utf-8")
    (working / "index.html").write_text("local edit", encoding="utf-8")
    commands = []

    def runner(command, **kwargs):
        commands.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    args = verify_clean_install.build_parser().parse_args(
        ["--full", "--apply", "--skip-node", "--skip-tests"]
    )
    assert verify_clean_install.run_full(root, args, runner)
    assert (working / "index.html").read_text(encoding="utf-8") == "fixture"
    assert commands
    assert all(isinstance(command, list) for command, _ in commands)
    assert all(kwargs["shell"] is False for _, kwargs in commands)
