from contextlib import nullcontext
from types import SimpleNamespace

from agentorchestra.scripts import probe_playwright


class FakeBrowser:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self, executable_path: str, browser: FakeBrowser) -> None:
        self.executable_path = executable_path
        self.browser = browser
        self.launch_calls: list[dict[str, bool]] = []

    def launch(self, **kwargs):
        self.launch_calls.append(kwargs)
        return self.browser


def test_probe_launches_and_closes_chromium_before_reporting_success(
    tmp_path, monkeypatch, capsys
):
    executable = tmp_path / "chromium"
    executable.write_bytes(b"browser")
    browser = FakeBrowser()
    chromium = FakeChromium(str(executable), browser)
    monkeypatch.setattr(
        probe_playwright,
        "sync_playwright",
        lambda: nullcontext(SimpleNamespace(chromium=chromium)),
    )

    assert probe_playwright.main() == 0
    assert chromium.launch_calls == [{"headless": True}]
    assert browser.closed is True
    assert capsys.readouterr() == (f"{probe_playwright.SUCCESS_TOKEN}\n", "")


def test_probe_rejects_a_missing_chromium_executable(tmp_path, monkeypatch, capsys):
    browser = FakeBrowser()
    chromium = FakeChromium(str(tmp_path / "missing-chromium"), browser)
    monkeypatch.setattr(
        probe_playwright,
        "sync_playwright",
        lambda: nullcontext(SimpleNamespace(chromium=chromium)),
    )

    assert probe_playwright.main() == 1
    assert chromium.launch_calls == []
    assert browser.closed is False
    assert capsys.readouterr() == ("", "")
