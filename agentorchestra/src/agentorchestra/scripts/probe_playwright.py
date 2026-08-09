from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

SUCCESS_TOKEN = "chromium-executable-ready"


def main() -> int:
    """Report whether Playwright's Chromium executable exists after a clean context exit."""
    try:
        with sync_playwright() as playwright:
            available = Path(playwright.chromium.executable_path).is_file()
    except Exception:
        return 1
    if not available:
        return 1
    print(SUCCESS_TOKEN)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
