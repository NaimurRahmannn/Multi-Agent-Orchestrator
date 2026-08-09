from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

SUCCESS_TOKEN = "chromium-executable-ready"


def main() -> int:
    """Report whether Playwright Chromium launches and shuts down cleanly."""
    try:
        with sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).is_file():
                return 1
            browser = playwright.chromium.launch(headless=True)
            browser.close()
    except Exception:
        return 1
    print(SUCCESS_TOKEN)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
