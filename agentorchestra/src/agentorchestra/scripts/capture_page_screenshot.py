from __future__ import annotations

import argparse
import uuid
from collections.abc import Sequence

from agentorchestra.config import Settings, get_settings
from agentorchestra.models import EditRequest
from agentorchestra.screenshot_models import ScreenshotKind, ScreenshotStatus
from agentorchestra.services.screenshots import capture_page_screenshot
from agentorchestra.services.site_digest import compute_site_tree_digest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture one local-only screenshot of a working sample-site page."
    )
    parser.add_argument("--target-page", required=True)
    return parser


def main(argv: Sequence[str] | None = None, *, settings: Settings | None = None) -> int:
    args = build_parser().parse_args(argv)
    resolved = settings or get_settings()
    request = EditRequest(
        target_page=args.target_page,
        instruction="Capture a direct working-site screenshot.",
    )
    digest = compute_site_tree_digest(resolved.working_site_dir)
    result = capture_page_screenshot(
        settings=resolved,
        site_root=resolved.working_site_dir,
        target_page=request.target_page,
        run_id=f"manual-{uuid.uuid4().hex}",
        kind=ScreenshotKind.BEFORE,
        source_site_digest=digest.digest,
    )
    print(f"screenshot status: {result.status.value}")
    if result.status is ScreenshotStatus.SUCCEEDED:
        print(f"screenshot path: {result.relative_path}")
        return 0
    print(f"error: {result.error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
