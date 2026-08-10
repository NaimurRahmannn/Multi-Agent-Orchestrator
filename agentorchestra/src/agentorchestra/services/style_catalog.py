from __future__ import annotations

import json
import re
from importlib.resources import files

from pydantic import TypeAdapter

from agentorchestra.style_models import (
    StyleAmount,
    StyleComponent,
    StyleIntentPlan,
    StyleOperation,
)

_COMPONENT_ADAPTER = TypeAdapter(list[StyleComponent])

_COLOR_VALUES = {
    "red": "#dc2626",
    "dark red": "#991b1b",
    "green": "#16a34a",
    "dark green": "#166534",
    "blue": "#2563eb",
    "dark blue": "#1e3a8a",
    "light blue": "#dbeafe",
    "gray": "#9ca3af",
    "grey": "#9ca3af",
    "light gray": "#e5e7eb",
    "light grey": "#e5e7eb",
    "dark gray": "#374151",
    "dark grey": "#374151",
    "white": "#ffffff",
    "black": "#000000",
    "yellow": "#eab308",
    "orange": "#f97316",
    "purple": "#9333ea",
    "pink": "#ec4899",
}


def load_style_components() -> tuple[StyleComponent, ...]:
    resource = files("agentorchestra").joinpath("data/site_components.json")
    payload = json.loads(resource.read_text(encoding="utf-8"))
    components = tuple(_COMPONENT_ADAPTER.validate_python(payload["components"]))
    ids = [component.id for component in components]
    if len(ids) != len(set(ids)):
        raise ValueError("Style component catalog IDs must be unique.")
    if any(component.page not in {"*", "index.html", "about.html", "contact.html"} for component in components):
        raise ValueError("Style component catalog contains an unsupported page.")
    return components


def components_for_page(target_page: str) -> tuple[StyleComponent, ...]:
    return tuple(
        component
        for component in load_style_components()
        if component.page in {"*", target_page}
    )


def catalog_prompt_payload(target_page: str) -> list[dict[str, object]]:
    return [
        {
            "target_id": component.id,
            "label": component.label,
            "aliases": component.aliases,
            "supported_operations": [operation.value for operation in component.operations],
        }
        for component in components_for_page(target_page)
    ]


def deterministic_style_plan(
    *,
    target_page: str,
    instruction: str,
) -> StyleIntentPlan | None:
    """Return a high-confidence semantic plan or None for the model planner."""
    normalized = " ".join(instruction.casefold().split())
    candidates = _target_candidates(normalized, target_page)
    if not candidates:
        return None
    best_score = max(score for score, _component in candidates)
    best = [component for score, component in candidates if score == best_score]
    if len(best) > 1:
        labels = ", ".join(component.label for component in best)
        return StyleIntentPlan(
            status="clarification_required",
            summary="More than one page element matches the request.",
            clarification_question=f"Which element should change: {labels}?",
        )
    component = best[0]
    operation, value = _operation_from_instruction(normalized)
    if operation is None or operation not in component.operations:
        return None
    return StyleIntentPlan(
        status="execute",
        target_id=component.id,
        operation=operation,
        value=value,
        amount=_amount_from_instruction(normalized),
        summary=f"Apply {operation.value} to {component.label}.",
    )


def normalize_color_value(value: str) -> str | None:
    normalized = " ".join(value.casefold().split())
    if re.fullmatch(r"#(?:[0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})", normalized):
        return normalized
    return _COLOR_VALUES.get(normalized)


def _target_candidates(
    instruction: str,
    target_page: str,
) -> list[tuple[int, StyleComponent]]:
    candidates: list[tuple[int, StyleComponent]] = []
    for component in components_for_page(target_page):
        matched = [alias for alias in component.aliases if _contains_phrase(instruction, alias)]
        if matched:
            candidates.append((max(len(alias) for alias in matched), component))
    return candidates


def _operation_from_instruction(
    instruction: str,
) -> tuple[StyleOperation | None, str | None]:
    color = _extract_color(instruction)
    if color is not None and any(term in instruction for term in ("text", "font", "letter")):
        return StyleOperation.SET_TEXT_COLOR, color
    if color is not None and any(term in instruction for term in ("background", "button", "page", "header", "footer")):
        return StyleOperation.SET_BACKGROUND_COLOR, color
    if "rounded" in instruction or "rounder" in instruction or "corner radius" in instruction:
        if any(term in instruction for term in ("less rounded", "square", "sharper")):
            return StyleOperation.DECREASE_BORDER_RADIUS, None
        return StyleOperation.INCREASE_BORDER_RADIUS, None
    if any(term in instruction for term in ("shorter", "reduce the height", "decrease the height")):
        return StyleOperation.DECREASE_HEIGHT, None
    if any(term in instruction for term in ("taller", "increase the height")):
        return StyleOperation.INCREASE_HEIGHT, None
    if any(term in instruction for term in ("spacing", "space between", "gap")):
        if any(term in instruction for term in ("decrease", "reduce", "less", "smaller")):
            return StyleOperation.DECREASE_GAP, None
        return StyleOperation.INCREASE_GAP, None
    if "padding" in instruction or "space around" in instruction:
        if any(term in instruction for term in ("decrease", "reduce", "less", "smaller")):
            return StyleOperation.DECREASE_PADDING, None
        return StyleOperation.INCREASE_PADDING, None
    if any(term in instruction for term in ("larger", "bigger", "increase the size")):
        return StyleOperation.INCREASE_FONT_SIZE, None
    if any(term in instruction for term in ("smaller", "decrease the size")):
        return StyleOperation.DECREASE_FONT_SIZE, None
    return None, None


def _extract_color(instruction: str) -> str | None:
    candidates: list[tuple[int, int, str]] = []
    for match in re.finditer(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b", instruction):
        candidates.append((match.end(), len(match.group(0)), match.group(0).lower()))
    for name, value in _COLOR_VALUES.items():
        for match in re.finditer(
            rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])",
            instruction,
        ):
            candidates.append((match.end(), len(name), value))
    return max(candidates, default=(0, 0, None), key=lambda item: (item[0], item[1]))[2]


def _amount_from_instruction(instruction: str) -> StyleAmount:
    if any(term in instruction for term in ("slightly", "a little", "small amount")):
        return StyleAmount.SLIGHT
    if any(term in instruction for term in ("much ", "significantly", "large amount")):
        return StyleAmount.LARGE
    return StyleAmount.MODERATE


def _contains_phrase(content: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase.casefold())}(?![a-z0-9])", content) is not None
