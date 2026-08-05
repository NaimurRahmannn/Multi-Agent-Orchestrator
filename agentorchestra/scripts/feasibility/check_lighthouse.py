from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.feasibility._preview_server import preview_server


def main() -> int:
    site_root = PROJECT_ROOT / "sites" / "fixture"
    output_path = PROJECT_ROOT / "reports" / "lighthouse" / "seo.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    chrome_flags = os.getenv("LIGHTHOUSE_CHROME_FLAGS", "").strip()
    npx = shutil.which("npx") or "npx"

    with preview_server(site_root) as base_url:
        command = [
            npx,
            "lighthouse",
            f"{base_url}/index.html",
            "--only-categories=seo",
            "--output=json",
            f"--output-path={output_path}",
            "--chrome-flags=--headless=new" + (f" {chrome_flags}" if chrome_flags else ""),
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired:
            print("FAIL Lighthouse timed out after 120 seconds")
            return 1

    if completed.returncode != 0:
        print("FAIL Lighthouse SEO check")
        print((completed.stderr or completed.stdout).strip())
        return 1

    try:
        report = json.loads(output_path.read_text(encoding="utf-8"))
        score = report["categories"]["seo"]["score"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"FAIL Lighthouse report did not contain an SEO category: {exc}")
        return 1

    print(f"PASS Lighthouse SEO score: {score:.2f}")
    print(f"Report: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
