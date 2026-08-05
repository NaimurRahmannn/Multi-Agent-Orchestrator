from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def _localize_crewai_paths() -> None:
    base = Path(tempfile.gettempdir()) / "agentorchestra-crewai"
    os.environ.setdefault("XDG_DATA_HOME", str(base / "data"))
    os.environ.setdefault("XDG_CONFIG_HOME", str(base / "config"))
    os.environ.setdefault("XDG_CACHE_HOME", str(base / "cache"))


def _import_check(module_name: str) -> CheckResult:
    try:
        if module_name == "crewai":
            _localize_crewai_paths()
        __import__(module_name)
    except Exception as exc:
        return CheckResult(module_name, False, f"Import failed: {exc}")
    return CheckResult(module_name, True, "imported")


def _command_check(name: str, command: list[str], timeout: int = 15) -> CheckResult:
    resolved = shutil.which(command[0])
    if not resolved:
        return CheckResult(name, False, f"{command[0]} is not on PATH")
    try:
        completed = subprocess.run(
            [resolved, *command[1:]],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(name, False, f"Timed out after {timeout} seconds")
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        return CheckResult(name, False, detail or f"{command[0]} exited with {completed.returncode}")
    version = (completed.stdout or completed.stderr).strip().splitlines()[0]
    return CheckResult(name, True, version)


def _playwright_chromium_check() -> CheckResult:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            browser.close()
    except Exception as exc:
        return CheckResult(
            "Playwright Chromium",
            False,
            f"Chromium launch failed. Run `playwright install chromium`. Details: {exc}",
        )
    return CheckResult("Playwright Chromium", True, "launched")


def run_checks() -> list[CheckResult]:
    results: list[CheckResult] = []
    python_ok = sys.version_info >= (3, 10) and sys.version_info < (3, 14)
    results.append(
        CheckResult(
            "Python",
            python_ok,
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        )
    )
    for module in ("crewai", "groq", "pydantic", "streamlit", "dotenv", "playwright"):
        results.append(_import_check(module))
    results.extend(
        [
            _command_check("node", ["node", "--version"]),
            _command_check("npm", ["npm", "--version"]),
            _command_check("npx", ["npx", "--version"]),
            _command_check("Lighthouse", ["npx", "lighthouse", "--version"], timeout=30),
            _playwright_chromium_check(),
        ]
    )
    return results


def main() -> int:
    results = run_checks()
    for result in results:
        marker = "PASS" if result.ok else "FAIL"
        print(f"{marker} {result.name}: {result.detail}")
    failed = [result for result in results if not result.ok]
    print(f"Summary: {len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
