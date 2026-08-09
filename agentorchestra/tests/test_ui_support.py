import subprocess

from agentorchestra.services.ui_support import (
    CHROMIUM_PROBE_SUCCESS,
    _probe_chromium_subprocess,
    check_runtime_readiness,
    default_target_page,
    list_supported_target_pages,
)
from tests.test_workspace_service import make_settings


def test_supported_target_pages_are_safe_top_level_and_sorted(tmp_path):
    settings = make_settings(tmp_path)
    assert list_supported_target_pages(settings) == ("about.html", "contact.html", "index.html")


def test_default_target_page_prefers_index_when_available():
    assert default_target_page(("about.html", "contact.html", "index.html")) == "index.html"


def test_default_target_page_falls_back_to_first_entry():
    assert default_target_page(("about.html", "contact.html")) == "about.html"


def test_readiness_uses_booleans_without_browser_launch_or_keys(tmp_path):
    settings = make_settings(tmp_path)
    calls = []

    def browser_probe():
        calls.append(1)
        return False

    cheap = check_runtime_readiness(settings, chromium_probe=browser_probe)
    assert cheap.chromium_available is None
    assert calls == []
    checked = check_runtime_readiness(
        settings,
        check_chromium=True,
        chromium_probe=browser_probe,
    )
    assert checked.chromium_available is False
    assert calls == [1]
    assert "api_key" not in checked.model_dump_json()


def test_chromium_subprocess_probe_requires_clean_stderr_and_drains_output(tmp_path, capsys):
    settings = make_settings(tmp_path)
    calls = []

    def dirty_runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=f"{CHROMIUM_PROBE_SUCCESS}\n",
            stderr="playwright shutdown failed\n",
        )

    assert _probe_chromium_subprocess(settings, runner=dirty_runner) is False
    assert capsys.readouterr() == ("", "")
    command, kwargs = calls[0]
    assert command[0]
    assert command[1].endswith("probe_playwright.py")
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.PIPE
    assert kwargs["stderr"] is subprocess.PIPE
    assert kwargs["check"] is False
    assert kwargs["shell"] is False


def test_chromium_subprocess_probe_accepts_only_exact_success_token(tmp_path):
    settings = make_settings(tmp_path)

    def clean_runner(command, **_kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=f"{CHROMIUM_PROBE_SUCCESS}\n",
            stderr="",
        )

    assert _probe_chromium_subprocess(settings, runner=clean_runner) is True
