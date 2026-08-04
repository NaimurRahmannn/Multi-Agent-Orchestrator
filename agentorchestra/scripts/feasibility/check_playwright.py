from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from playwright.sync_api import sync_playwright

from scripts.feasibility._preview_server import preview_server


def main() -> int:
    site_root = PROJECT_ROOT / "sites" / "fixture"
    output_path = PROJECT_ROOT / "reports" / "screenshots" / "index.png"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with preview_server(site_root) as base_url, sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            try:
                page.goto(f"{base_url}/index.html", wait_until="load")
                page.wait_for_load_state("networkidle")
                page.screenshot(path=str(output_path), full_page=True)
            finally:
                page.close()
                browser.close()
    except Exception as exc:
        print(f"FAIL Playwright screenshot: {exc}")
        return 1

    if not output_path.exists() or output_path.stat().st_size == 0:
        print(f"FAIL Screenshot was not written: {output_path}")
        return 1
    print(f"PASS Playwright screenshot: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
