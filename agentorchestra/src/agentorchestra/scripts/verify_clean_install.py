from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path

REQUIRED_ENV = {
    "GROQ_MANAGER_API_KEY",
    "GROQ_MANAGER_MODEL",
    "GROQ_HTML_API_KEY",
    "GROQ_HTML_MODEL",
    "GROQ_CSS_API_KEY",
    "GROQ_CSS_MODEL",
    "GROQ_SEO_API_KEY",
    "GROQ_SEO_MODEL",
    "GROQ_QA_API_KEY",
    "GROQ_QA_MODEL",
    "APP_ENV",
    "LOG_LEVEL",
    "AGENTORCHESTRA_ROOT",
}
REQUIRED_FILES = {
    "README.md",
    "pyproject.toml",
    "uv.lock",
    "package.json",
    "package-lock.json",
    ".env.example",
    ".gitignore",
    "docs/setup.md",
    "docs/usage.md",
    "docs/architecture.md",
    "docs/troubleshooting.md",
    "docs/demo.md",
    "scripts/run_demo.py",
    "scripts/verify_clean_install.py",
}
EXCLUDED_NAMES = {
    ".git",
    ".venv",
    "node_modules",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    ".env",
}

Runner = Callable[..., subprocess.CompletedProcess[str]]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify AgentOrchestra from an isolated copy.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Run offline repository checks.")
    mode.add_argument("--full", action="store_true", help="Install and test an isolated copy.")
    parser.add_argument("--apply", action="store_true", help="Required with --full.")
    parser.add_argument("--skip-node", action="store_true")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--install-browser", action="store_true")
    parser.add_argument("--keep-temp-on-failure", action="store_true")
    return parser


def _ignore(directory: str, names: list[str]) -> set[str]:
    root = Path(directory)
    ignored = {name for name in names if name in EXCLUDED_NAMES or name.endswith(".pyc")}
    relative = root.as_posix()
    if relative.endswith("/reports"):
        ignored.update(name for name in names if name in {"lighthouse", "screenshots", "routing"})
    if relative.endswith("/sites/staging"):
        ignored.update(name for name in names if name != ".gitkeep")
    ignored.update(name for name in names if ".agentorchestra-" in name)
    return ignored


def copy_source(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, ignore=_ignore)


def _markdown_links(text: str) -> list[str]:
    return re.findall(r"(?<!!)\[[^]]+\]\(([^)]+)\)", text)


def _mermaid_fences_closed(text: str) -> bool:
    in_mermaid = False
    for line in text.splitlines():
        marker = line.strip()
        if marker == "```mermaid" and not in_mermaid:
            in_mermaid = True
        elif marker == "```" and in_mermaid:
            in_mermaid = False
    return not in_mermaid


def _site_trees_match(first: Path, second: Path) -> bool:
    """Return whether two non-symlink directory trees have identical entries and bytes."""
    try:
        if (
            first.is_symlink()
            or second.is_symlink()
            or not first.is_dir()
            or not second.is_dir()
        ):
            return False
        first_entries = {
            path.relative_to(first): "directory" if path.is_dir() else "file"
            for path in first.rglob("*")
            if not path.is_symlink() and (path.is_dir() or path.is_file())
        }
        second_entries = {
            path.relative_to(second): "directory" if path.is_dir() else "file"
            for path in second.rglob("*")
            if not path.is_symlink() and (path.is_dir() or path.is_file())
        }
        if first_entries != second_entries:
            return False
        if any(path.is_symlink() for path in first.rglob("*")) or any(
            path.is_symlink() for path in second.rglob("*")
        ):
            return False
        return all(
            (first / relative).read_bytes() == (second / relative).read_bytes()
            for relative, entry_type in first_entries.items()
            if entry_type == "file"
        )
    except OSError:
        return False


def offline_checks(root: Path) -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []
    missing = sorted(path for path in REQUIRED_FILES if not (root / path).is_file())
    checks.append(("required files", not missing, ", ".join(missing) or "present"))
    try:
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        metadata_ok = project.get("project", {}).get("name") == "agentorchestra"
    except Exception:
        metadata_ok = False
    checks.append(("Python metadata", metadata_ok, "valid" if metadata_ok else "invalid"))

    env_text = (root / ".env.example").read_text(encoding="utf-8") if (root / ".env.example").is_file() else ""
    env_names = {
        line.split("=", 1)[0].lstrip("# ").strip()
        for line in env_text.splitlines()
        if "=" in line
    }
    missing_env = sorted(REQUIRED_ENV - env_names)
    checks.append(("environment variables", not missing_env, ", ".join(missing_env) or "documented"))

    readme = (root / "README.md").read_text(encoding="utf-8") if (root / "README.md").is_file() else ""
    broken: list[str] = []
    for link in _markdown_links(readme):
        target = link.split("#", 1)[0]
        if not target or "://" in target or target.startswith("#"):
            continue
        if not (root / target).exists():
            broken.append(link)
    checks.append(("README links", not broken, ", ".join(broken) or "resolved"))

    docs = [readme]
    docs.extend(path.read_text(encoding="utf-8") for path in (root / "docs").glob("*.md"))
    combined = "\n".join(docs)
    checks.append(("Mermaid fences", _mermaid_fences_closed(combined), "balanced"))
    forbidden_path = re.search(r"(?:[A-Za-z]:\\Users\\|/Users/|/home/)[^\s)`]+", combined)
    checks.append(("absolute local paths", forbidden_path is None, "none found"))
    secret = re.search(r"\bgsk_[A-Za-z0-9]{20,}\b", combined + "\n" + env_text)
    checks.append(("real-looking secrets", secret is None, "none found"))

    ignore_text = (root / ".gitignore").read_text(encoding="utf-8")
    ignore_ok = all(value in ignore_text for value in (".env", "node_modules/", "reports/lighthouse/*", "sites/staging/*"))
    checks.append(("generated-file ignores", ignore_ok, "configured" if ignore_ok else "incomplete"))
    fixture_root = root / "sites" / "fixture"
    working_root = root / "sites" / "working"
    fixture_ok = all((fixture_root / name).is_file() for name in ("index.html", "about.html", "contact.html", "style.css"))
    working_ok = all((working_root / name).is_file() for name in ("index.html", "about.html", "contact.html", "style.css"))
    checks.append(("sample-site structure", fixture_ok and working_ok, "valid" if fixture_ok and working_ok else "invalid"))
    baseline_ok = fixture_ok and working_ok and _site_trees_match(fixture_root, working_root)
    checks.append(
        (
            "sample-site baseline",
            baseline_ok,
            "working matches fixture" if baseline_ok else "working differs from fixture",
        )
    )
    return checks


def _run(command: list[str], *, cwd: Path, runner: Runner) -> bool:
    def safe(value: str) -> str:
        return value.replace(str(cwd), "[isolated-copy]").replace(
            cwd.as_posix(), "[isolated-copy]"
        )

    print(f"run: {safe(' '.join(command))}")
    completed = runner(
        command,
        cwd=cwd,
        shell=False,
        check=False,
        text=True,
        capture_output=True,
    )
    stdout = safe(getattr(completed, "stdout", "") or "").strip()
    stderr = safe(getattr(completed, "stderr", "") or "").strip()
    if stdout:
        print(stdout)
    if stderr:
        print(stderr)
    print(f"exit: {completed.returncode}")
    return completed.returncode == 0


def _python_in_venv(root: Path) -> Path:
    return root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _executable(name: str) -> str:
    return shutil.which(name) or name


def run_full(root: Path, args: argparse.Namespace, runner: Runner) -> bool:
    # A clean clone starts from the committed fixture baseline. Normalize only
    # the disposable copy so local accepted edits in the source stay untouched.
    working = root / "sites" / "working"
    fixture = root / "sites" / "fixture"
    shutil.rmtree(working)
    shutil.copytree(fixture, working)
    ok = _run([_executable("uv"), "sync", "--frozen"], cwd=root, runner=runner)
    if not ok:
        return False
    python = str(_python_in_venv(root))
    if not args.skip_node:
        ok = _run([_executable("npm"), "ci"], cwd=root, runner=runner) and ok
    ok = _run([python, "-c", "import agentorchestra, crewai, streamlit, playwright"], cwd=root, runner=runner) and ok
    for script in ("run_demo.py", "verify_clean_install.py", "run_edit_flow.py", "reset_demo_site.py", "run_ui.py"):
        ok = _run([python, f"scripts/{script}", "--help"], cwd=root, runner=runner) and ok
    if args.install_browser:
        ok = _run([python, "-m", "playwright", "install", "chromium"], cwd=root, runner=runner) and ok
    if not args.skip_tests:
        ok = _run([python, "-m", "pytest", "-q"], cwd=root, runner=runner) and ok
    ok = _run([python, "-m", "ruff", "check", "."], cwd=root, runner=runner) and ok
    return ok


def main(
    argv: Sequence[str] | None = None,
    *,
    source_root: Path | None = None,
    runner: Runner = subprocess.run,
) -> int:
    args = build_parser().parse_args(argv)
    if args.full and not args.apply:
        print("No full verification was run. Re-run --full with --apply.")
        return 2
    source = (source_root or Path(__file__).resolve().parents[3]).resolve()
    # A sibling temp directory keeps the source untouched and avoids Windows
    # profiles where the system temp root can be created but not populated.
    temporary = Path(
        tempfile.mkdtemp(prefix="agentorchestra-verify-", dir=source.parent)
    )
    copy = temporary / "agentorchestra"
    success = False
    try:
        copy_source(source, copy)
        checks = offline_checks(copy)
        for name, passed, detail in checks:
            print(f"- {name}: {'passed' if passed else 'failed'} ({detail})")
        success = all(item[1] for item in checks)
        if args.full and success:
            success = run_full(copy, args, runner)
        print(f"verification: {'passed' if success else 'failed'}")
        return 0 if success else 1
    finally:
        if args.keep_temp_on_failure and not success:
            print(f"temporary copy preserved: {temporary}")
        else:
            shutil.rmtree(temporary, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
