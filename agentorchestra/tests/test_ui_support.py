from contextlib import contextmanager
from types import SimpleNamespace

from agentorchestra.services.ui_support import (
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

    @contextmanager
    def browser_probe():
        calls.append(1)
        yield SimpleNamespace(
            chromium=SimpleNamespace(executable_path=str(tmp_path / "chromium.exe"))
        )

    cheap = check_runtime_readiness(settings, playwright_factory=browser_probe)
    assert cheap.chromium_available is None
    assert calls == []
    checked = check_runtime_readiness(
        settings,
        check_chromium=True,
        playwright_factory=browser_probe,
    )
    assert checked.chromium_available is False
    assert calls == [1]
    assert "api_key" not in checked.model_dump_json()
