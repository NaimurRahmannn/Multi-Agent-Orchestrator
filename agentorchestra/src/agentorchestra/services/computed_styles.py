from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, suppress
from pathlib import Path
from typing import Any

from agentorchestra.config import Settings, get_settings
from agentorchestra.services.preview_server import serve_site
from agentorchestra.services.screenshots import (
    NAVIGATION_TIMEOUT_MS,
    _launch_chromium,
    _loopback_origin,
    _route_local_only,
    _sync_playwright_factory,
    _validate_capture_inputs,
)
from agentorchestra.style_models import StyleChangeEvidence

PlaywrightFactory = Callable[[], AbstractContextManager[Any]]
PreviewFactory = Callable[[Path], AbstractContextManager[str]]

_COMPUTED_VALUE_SCRIPT = """
([selector, propertyName, beforeValue, afterValue]) => {
  const element = document.querySelector(selector);
  if (!element) return null;
  const computedProperty = propertyName === "background" ? "background-color" : propertyName;
  const normalize = (sourceValue) => {
    const probe = element.cloneNode(false);
    probe.removeAttribute("id");
    probe.style.setProperty(propertyName, sourceValue, "important");
    probe.style.position = "absolute";
    probe.style.visibility = "hidden";
    probe.style.pointerEvents = "none";
    document.body.appendChild(probe);
    const value = getComputedStyle(probe).getPropertyValue(computedProperty).trim();
    probe.remove();
    return value;
  };
  return {
    actualAfter: getComputedStyle(element).getPropertyValue(computedProperty).trim(),
    expectedBefore: normalize(beforeValue),
    expectedAfter: normalize(afterValue),
  };
}
""".strip()


def verify_computed_style_evidence(
    *,
    settings: Settings | None = None,
    site_root: Path,
    target_page: str,
    run_id: str,
    changes: Sequence[StyleChangeEvidence],
    playwright_factory: PlaywrightFactory | None = None,
    preview_factory: PreviewFactory = serve_site,
) -> list[StyleChangeEvidence]:
    """Verify installed style declarations against browser-computed values."""
    if not changes:
        return []
    resolved = settings or get_settings()
    target = _validate_capture_inputs(
        settings=resolved,
        site_root=site_root,
        target_page=target_page,
        run_id=run_id,
    )
    factory = playwright_factory or _sync_playwright_factory
    page = context = browser = None
    try:
        with preview_factory(target) as base_url, factory() as playwright:
            browser = _launch_chromium(playwright.chromium)
            context = browser.new_context()
            page = context.new_page()
            origin = _loopback_origin(base_url)
            page.route("**/*", lambda route: _route_local_only(route, origin))
            page.goto(
                f"{base_url.rstrip('/')}/{target_page}",
                wait_until="domcontentloaded",
                timeout=NAVIGATION_TIMEOUT_MS,
            )
            page.wait_for_load_state("load", timeout=5_000)
            return [_verify_one(page, change) for change in changes]
    finally:
        for resource in (page, context, browser):
            if resource is not None:
                with suppress(Exception):
                    resource.close()


def _verify_one(page: Any, change: StyleChangeEvidence) -> StyleChangeEvidence:
    values = page.evaluate(
        _COMPUTED_VALUE_SCRIPT,
        [
            change.selector,
            change.property_name,
            change.before_value,
            change.after_value,
        ],
    )
    if not isinstance(values, dict):
        return change.model_copy(update={"computed_verified": False})
    before = str(values.get("expectedBefore") or "").strip()
    after = str(values.get("actualAfter") or "").strip()
    expected_after = str(values.get("expectedAfter") or "").strip()
    verified = bool(after and expected_after and _normalize(after) == _normalize(expected_after))
    if verified and change.expected_relation in {"increased", "decreased"}:
        verified = _numeric_relation(before, after, change.expected_relation)
    return change.model_copy(
        update={
            "computed_before_value": before or None,
            "computed_after_value": after or None,
            "computed_verified": verified,
        }
    )


def _numeric_relation(before: str, after: str, relation: str) -> bool:
    before_numbers = _numbers(before)
    after_numbers = _numbers(after)
    if not before_numbers or len(before_numbers) != len(after_numbers):
        return False
    if relation == "increased":
        return any(right > left for left, right in zip(before_numbers, after_numbers, strict=True))
    return any(right < left for left, right in zip(before_numbers, after_numbers, strict=True))


def _numbers(value: str) -> tuple[float, ...]:
    import re

    return tuple(float(item) for item in re.findall(r"-?\d+(?:\.\d+)?", value))


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())
