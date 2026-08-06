from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentorchestra.exceptions import ScreenshotSafetyError
from agentorchestra.screenshot_models import ScreenshotStatus
from agentorchestra.services.screenshots import (
    NAVIGATION_TIMEOUT_MS,
    PNG_SIGNATURE,
    capture_page_screenshot,
)
from agentorchestra.services.site_digest import compute_site_tree_digest
from agentorchestra.services.workspace import create_staged_copy
from tests.test_workspace_service import make_settings


class FakePage:
    def __init__(self, lifecycle, *, fail_navigation=False):
        self.lifecycle = lifecycle
        self.fail_navigation = fail_navigation
        self.handler = None
        self.goto_call = None
        self.screenshot_call = None

    def route(self, pattern, handler):
        self.handler = handler
        self.lifecycle.append(("route", pattern))

    def goto(self, url, **kwargs):
        self.goto_call = (url, kwargs)
        self.lifecycle.append("goto")
        if self.fail_navigation:
            raise RuntimeError("navigation failed with gsk_private_value")

    def screenshot(self, *, path, full_page):
        self.screenshot_call = (path, full_page)
        Path(path).write_bytes(PNG_SIGNATURE + b"image")
        self.lifecycle.append("screenshot")

    def wait_for_load_state(self, state, *, timeout):
        self.lifecycle.append(("wait", state, timeout))

    def close(self):
        self.lifecycle.append("page_closed")


class FakeContext:
    def __init__(self, page, lifecycle):
        self.page = page
        self.lifecycle = lifecycle

    def new_page(self):
        return self.page

    def close(self):
        self.lifecycle.append("context_closed")


class FakeBrowser:
    def __init__(self, page, lifecycle):
        self.page = page
        self.lifecycle = lifecycle
        self.viewport = None

    def new_context(self, *, viewport):
        self.viewport = viewport
        return FakeContext(self.page, self.lifecycle)

    def close(self):
        self.lifecycle.append("browser_closed")


class FakeChromium:
    def __init__(self, browser, lifecycle):
        self.browser = browser
        self.lifecycle = lifecycle
        self.executable_path = __file__

    def launch(self, *, headless):
        self.lifecycle.append(("launch", headless))
        return self.browser


def fake_playwright(lifecycle, *, fail_navigation=False):
    page = FakePage(lifecycle, fail_navigation=fail_navigation)
    browser = FakeBrowser(page, lifecycle)

    @contextmanager
    def factory():
        lifecycle.append("playwright_started")
        try:
            yield SimpleNamespace(chromium=FakeChromium(browser, lifecycle))
        finally:
            lifecycle.append("playwright_stopped")

    return factory, page, browser


@contextmanager
def fake_preview(_root):
    yield "http://127.0.0.1:43210"


def test_capture_working_page_uses_fixed_local_full_page_contract(tmp_path):
    settings = make_settings(tmp_path)
    before = compute_site_tree_digest(settings.working_site_dir)
    lifecycle = []
    factory, page, browser = fake_playwright(lifecycle)

    result = capture_page_screenshot(
        settings=settings,
        site_root=settings.working_site_dir,
        target_page="index.html",
        run_id="shot-run",
        kind="before",
        source_site_digest=before.digest,
        playwright_factory=factory,
        preview_factory=fake_preview,
        clock=iter([1.0, 1.01]).__next__,
    )

    assert result.status is ScreenshotStatus.SUCCEEDED
    assert result.relative_path == "reports/screenshots/shot-run/before-index.png"
    assert page.goto_call == (
        "http://127.0.0.1:43210/index.html",
        {"wait_until": "domcontentloaded", "timeout": NAVIGATION_TIMEOUT_MS},
    )
    assert page.screenshot_call[1] is True
    assert browser.viewport == {"width": 1440, "height": 900}
    assert lifecycle[-4:] == [
        "page_closed",
        "context_closed",
        "browser_closed",
        "playwright_stopped",
    ]
    assert compute_site_tree_digest(settings.working_site_dir) == before

    local = FakeRoute("http://127.0.0.1:43210/style.css")
    external = FakeRoute("https://example.com/font.woff2")
    page.handler(local)
    page.handler(external)
    assert local.action == "continued"
    assert external.action == "aborted"


def test_capture_accepts_matching_staged_root(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "stage-shot")
    digest = compute_site_tree_digest(handle.path)
    factory, _, _ = fake_playwright([])
    result = capture_page_screenshot(
        settings=settings,
        site_root=handle.path,
        target_page="contact.html",
        run_id=handle.run_id,
        kind="proposed_after",
        source_site_digest=digest.digest,
        playwright_factory=factory,
        preview_factory=fake_preview,
    )
    assert result.status is ScreenshotStatus.SUCCEEDED
    assert result.relative_path.endswith("/proposed-after-contact.png")


def test_navigation_failure_closes_resources_redacts_and_removes_partial_output(tmp_path):
    settings = make_settings(tmp_path)
    lifecycle = []
    factory, _, _ = fake_playwright(lifecycle, fail_navigation=True)
    result = capture_page_screenshot(
        settings=settings,
        site_root=settings.working_site_dir,
        target_page="index.html",
        run_id="failed-shot",
        kind="before",
        source_site_digest="a" * 64,
        playwright_factory=factory,
        preview_factory=fake_preview,
    )
    assert result.status is ScreenshotStatus.FAILED
    assert "private_value" not in result.error
    assert not (settings.screenshot_report_dir / "failed-shot").exists()
    assert "page_closed" in lifecycle
    assert "context_closed" in lifecycle
    assert "browser_closed" in lifecycle
    assert "playwright_stopped" in lifecycle


def test_missing_playwright_or_chromium_returns_failed_artifact(tmp_path):
    settings = make_settings(tmp_path)

    @contextmanager
    def unavailable():
        raise ImportError("Playwright unavailable")
        yield

    result = capture_page_screenshot(
        settings=settings,
        site_root=settings.working_site_dir,
        target_page="index.html",
        run_id="missing-runtime",
        kind="before",
        source_site_digest="a" * 64,
        playwright_factory=unavailable,
        preview_factory=fake_preview,
    )
    assert result.status is ScreenshotStatus.FAILED
    assert result.relative_path is None


@pytest.mark.parametrize("page,run_id", [("../index.html", "safe"), ("index.html", "../bad")])
def test_capture_rejects_unsafe_page_or_run_before_browser(tmp_path, page, run_id):
    settings = make_settings(tmp_path)
    calls = []

    @contextmanager
    def forbidden():
        calls.append(1)
        yield

    with pytest.raises(ScreenshotSafetyError):
        capture_page_screenshot(
            settings=settings,
            site_root=settings.working_site_dir,
            target_page=page,
            run_id=run_id,
            kind="before",
            source_site_digest="a" * 64,
            playwright_factory=forbidden,
            preview_factory=fake_preview,
        )
    assert calls == []


def test_capture_rejects_untrusted_site_root(tmp_path):
    settings = make_settings(tmp_path / "project")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "index.html").write_text("<html></html>", encoding="utf-8")
    (outside / "contact.html").write_text("<html></html>", encoding="utf-8")
    (outside / "style.css").write_text("body {}", encoding="utf-8")
    (outside / "assets").mkdir()
    with pytest.raises(ScreenshotSafetyError):
        capture_page_screenshot(
            settings=settings,
            site_root=outside,
            target_page="index.html",
            run_id="outside",
            kind="before",
            source_site_digest="a" * 64,
            playwright_factory=lambda: None,
            preview_factory=fake_preview,
        )


class FakeRoute:
    def __init__(self, url):
        self.request = SimpleNamespace(url=url)
        self.action = None

    def continue_(self):
        self.action = "continued"

    def abort(self):
        self.action = "aborted"
