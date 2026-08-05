from __future__ import annotations

import argparse
from collections.abc import Sequence

from agentorchestra.config import Settings, get_settings
from agentorchestra.services.lighthouse import run_working_lighthouse_seo


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a Lighthouse SEO-only audit on sites/working."
    )
    parser.add_argument("--target-page", required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Required before launching the local preview server and Lighthouse.",
    )
    return parser


def main(argv: Sequence[str] | None = None, *, settings: Settings | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.apply:
        print("No audit was run. Re-run with --apply to allow Lighthouse execution.")
        return 2
    result = run_working_lighthouse_seo(args.target_page, settings=settings or get_settings())
    print(f"lighthouse status: {result.status.value}")
    print(f"target page: {result.target_page}")
    if result.status.value == "succeeded":
        print(f"seo score: {result.score}")
        print(f"failed audits: {', '.join(result.failed_audit_ids) or 'none'}")
        print(f"report: {result.report_path}")
        print(f"latency ms: {result.latency_ms:.1f}")
        return 0
    print(f"error: {result.error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
