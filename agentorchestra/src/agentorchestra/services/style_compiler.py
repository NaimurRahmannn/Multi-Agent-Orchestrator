from __future__ import annotations

import re

from agentorchestra.models import SpecialistName
from agentorchestra.services.style_catalog import components_for_page, normalize_color_value
from agentorchestra.services.workspace import read_file, update_css_declaration
from agentorchestra.style_models import (
    StyleAmount,
    StyleChangeEvidence,
    StyleComponent,
    StyleExecutionResult,
    StyleIntentPlan,
    StyleOperation,
    StylePlanStatus,
)
from agentorchestra.workspace_models import PatchStatus, WorkspaceHandle

_PX_TOKENS = (0.0, 4.0, 6.0, 8.0, 10.0, 12.0, 16.0, 18.0, 24.0, 32.0, 36.0, 48.0, 56.0, 64.0, 72.0, 96.0)
_HEIGHT_TOKENS = (240.0, 320.0, 420.0, 480.0, 520.0, 560.0, 640.0, 760.0)
_REM_TOKENS = (0.75, 0.875, 1.0, 1.15, 1.2, 1.5, 2.0, 2.2, 2.5, 3.0, 3.5, 4.0)


def execute_style_plan(
    plan: StyleIntentPlan,
    *,
    target_page: str,
    workspace: WorkspaceHandle,
) -> StyleExecutionResult:
    validated = StyleIntentPlan.model_validate(plan)
    if validated.status is StylePlanStatus.CLARIFICATION_REQUIRED:
        return StyleExecutionResult(
            status="clarification_required",
            plan=validated,
            summary=validated.summary,
            clarification_question=validated.clarification_question,
        )
    if validated.status is StylePlanStatus.UNSUPPORTED:
        return StyleExecutionResult(
            status="blocked",
            plan=validated,
            summary=validated.summary,
            remaining_issue=validated.reason,
        )

    component = _component_by_id(validated.target_id or "", target_page)
    if component is None:
        labels = ", ".join(item.label for item in components_for_page(target_page))
        question = f"Which page element should change? Available choices: {labels}."
        clarification = StyleIntentPlan(
            status="clarification_required",
            summary="The planned style target is not available on the selected page.",
            clarification_question=question,
        )
        return StyleExecutionResult(
            status="clarification_required",
            plan=clarification,
            summary=clarification.summary,
            clarification_question=question,
        )
    operation = validated.operation
    if operation is None or operation not in component.operations:
        return StyleExecutionResult(
            status="blocked",
            plan=validated,
            summary="The requested semantic style operation is unsupported for this element.",
            remaining_issue=(
                f"{operation.value if operation else 'unknown operation'} is not supported for "
                f"{component.label}."
            ),
        )

    property_name = component.operations[operation]
    current = _read_declaration(workspace, component.selector, property_name)
    if current is None:
        return StyleExecutionResult(
            status="blocked",
            plan=validated,
            summary="The cataloged CSS declaration is unavailable.",
            remaining_issue=(
                f"Property {property_name!r} was not found in catalog target {component.id!r}."
            ),
        )
    desired = _desired_value(validated, current, property_name)
    if desired is None:
        return StyleExecutionResult(
            status="blocked",
            plan=validated,
            summary="The requested style value could not be normalized safely.",
            remaining_issue=f"A supported value is required for {operation.value}.",
        )
    relation = _expected_relation(operation)
    evidence = StyleChangeEvidence(
        target_id=component.id,
        label=component.label,
        selector=component.selector,
        property_name=property_name,
        before_value=current,
        after_value=desired,
        expected_relation=relation,
        source_verified=True,
    )
    if _normalize_css_value(current) == _normalize_css_value(desired):
        return StyleExecutionResult(
            status="already_satisfied",
            plan=validated,
            summary=f"{component.label} already has the requested style.",
            evidence=evidence,
        )

    patch = update_css_declaration(
        workspace,
        specialist=SpecialistName.CSS,
        selector=component.selector,
        property_name=property_name,
        value=desired,
        summary=validated.summary,
        allowed_files=("style.css",),
    )
    if patch.status is not PatchStatus.APPLIED:
        return StyleExecutionResult(
            status="blocked",
            plan=validated,
            summary="The deterministic style compiler could not apply the planned edit.",
            remaining_issue=patch.message,
        )
    installed = _read_declaration(workspace, component.selector, property_name)
    if installed is None or _normalize_css_value(installed) != _normalize_css_value(desired):
        return StyleExecutionResult(
            status="blocked",
            plan=validated,
            summary="The deterministic style write could not be verified.",
            remaining_issue="The installed CSS declaration did not match the planned value.",
        )
    return StyleExecutionResult(
        status="applied",
        plan=validated,
        summary=f"Updated {component.label} {property_name} from {current} to {desired}.",
        patch=patch,
        evidence=evidence,
    )


def _component_by_id(target_id: str, target_page: str) -> StyleComponent | None:
    return next(
        (item for item in components_for_page(target_page) if item.id == target_id),
        None,
    )


def _read_declaration(
    workspace: WorkspaceHandle,
    selector: str,
    property_name: str,
) -> str | None:
    content = _read_stylesheet(workspace)
    rule_pattern = re.compile(
        rf"(?m)^[ \t]*{re.escape(selector)}[ \t]*\{{(?P<body>[^{{}}]*)\}}"
    )
    rules = list(rule_pattern.finditer(content))
    declaration_pattern = re.compile(
        rf"(?mi)^[ \t]*{re.escape(property_name)}[ \t]*:[ \t]*(?P<value>[^;\r\n{{}}]+)[ \t]*;"
    )
    values = [
        declaration.group("value").strip()
        for rule in rules
        for declaration in declaration_pattern.finditer(rule.group("body"))
    ]
    if len(values) != 1:
        return None
    return values[0]


def _read_stylesheet(workspace: WorkspaceHandle) -> str:
    chunks: list[str] = []
    start = 1
    while True:
        result = read_file(
            workspace,
            file="style.css",
            start_line=start,
            end_line=start + 119,
            allowed_files=("style.css",),
        )
        chunks.append(result.content)
        if not result.truncated:
            return "".join(chunks)
        start = result.end_line + 1


def _desired_value(
    plan: StyleIntentPlan,
    current: str,
    property_name: str,
) -> str | None:
    operation = plan.operation
    if operation in {
        StyleOperation.SET_BACKGROUND_COLOR,
        StyleOperation.SET_TEXT_COLOR,
    }:
        return normalize_color_value(plan.value or "")
    if operation in {
        StyleOperation.SET_BORDER_RADIUS,
        StyleOperation.SET_HEIGHT,
        StyleOperation.SET_GAP,
        StyleOperation.SET_FONT_SIZE,
        StyleOperation.SET_PADDING,
    }:
        return _validated_numeric_value(plan.value or "", multiple=operation is StyleOperation.SET_PADDING)
    direction = 1 if operation in {
        StyleOperation.INCREASE_BORDER_RADIUS,
        StyleOperation.INCREASE_HEIGHT,
        StyleOperation.INCREASE_GAP,
        StyleOperation.INCREASE_FONT_SIZE,
        StyleOperation.INCREASE_PADDING,
    } else -1
    steps = {
        StyleAmount.SLIGHT: 1,
        StyleAmount.MODERATE: 2,
        StyleAmount.LARGE: 3,
    }[plan.amount]
    if property_name == "min-height":
        return _step_single_numeric(current, _HEIGHT_TOKENS, direction, steps)
    if property_name == "font-size":
        return _step_single_numeric(current, _REM_TOKENS, direction, steps)
    if property_name in {"border-radius", "gap"}:
        return _step_single_numeric(current, _PX_TOKENS, direction, steps)
    if property_name == "padding":
        return _step_multi_numeric(current, _PX_TOKENS, direction, steps)
    return None


def _step_single_numeric(
    current: str,
    tokens: tuple[float, ...],
    direction: int,
    steps: int,
) -> str | None:
    parsed = _parse_numeric(current)
    if parsed is None:
        return None
    number, unit = parsed
    compatible = tokens
    index = min(range(len(compatible)), key=lambda item: abs(compatible[item] - number))
    target = compatible[max(0, min(len(compatible) - 1, index + direction * steps))]
    return f"{_format_number(target)}{unit}"


def _step_multi_numeric(
    current: str,
    tokens: tuple[float, ...],
    direction: int,
    steps: int,
) -> str | None:
    parts = current.split()
    updated = [_step_single_numeric(part, tokens, direction, steps) for part in parts]
    if not updated or any(item is None for item in updated):
        return None
    return " ".join(item for item in updated if item is not None)


def _parse_numeric(value: str) -> tuple[float, str] | None:
    match = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*(px|rem)\s*", value, flags=re.IGNORECASE)
    if match is None:
        return None
    return float(match.group(1)), match.group(2).lower()


def _validated_numeric_value(value: str, *, multiple: bool) -> str | None:
    parts = value.split()
    if not parts or (not multiple and len(parts) != 1) or len(parts) > 4:
        return None
    parsed = [_parse_numeric(part) for part in parts]
    if any(item is None or item[0] < 0 for item in parsed):
        return None
    return " ".join(
        f"{_format_number(number)}{unit}"
        for number, unit in parsed
        if number is not None and unit is not None
    )


def _format_number(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value).rstrip("0").rstrip(".")


def _expected_relation(operation: StyleOperation) -> str:
    if operation.value.startswith("increase_"):
        return "increased"
    if operation.value.startswith("decrease_"):
        return "decreased"
    return "equals_requested"


def _normalize_css_value(value: str) -> str:
    normalized = " ".join(value.casefold().split())
    return normalize_color_value(normalized) or normalized
