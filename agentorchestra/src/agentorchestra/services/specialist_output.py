from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ValidationError

from agentorchestra.exceptions import SpecialistOutputError
from agentorchestra.seo_models import SEOCompletion
from agentorchestra.specialist_models import SpecialistCompletion
from agentorchestra.style_models import StyleIntentPlan


def extract_specialist_completion(output: Any) -> SpecialistCompletion:
    """Extract only supported CrewAI output shapes and validate without repairing them."""
    try:
        return _extract(output)
    except SpecialistOutputError:
        raise
    except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise SpecialistOutputError(
            "Specialist response did not match the structured completion contract."
        ) from exc


def extract_seo_completion(output: Any) -> SEOCompletion:
    """Extract a strict SEO completion without repairing unsupported output."""
    try:
        return _extract_model(output, SEOCompletion)
    except SpecialistOutputError:
        raise
    except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise SpecialistOutputError(
            "SEO response did not match the structured completion contract."
        ) from exc


def extract_style_intent_plan(output: Any) -> StyleIntentPlan:
    """Extract one strict semantic CSS plan without repairing unsupported output."""
    try:
        return _extract_model(output, StyleIntentPlan)
    except SpecialistOutputError:
        raise
    except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise SpecialistOutputError(
            "CSS specialist response did not match the semantic style plan contract."
        ) from exc


def _extract(output: Any) -> SpecialistCompletion:
    if isinstance(output, SpecialistCompletion):
        return output
    if isinstance(output, Mapping):
        return SpecialistCompletion.model_validate(output)

    pydantic_output = getattr(output, "pydantic", None)
    if pydantic_output is not None:
        return _extract(pydantic_output)

    json_dict = getattr(output, "json_dict", None)
    if json_dict is not None:
        return SpecialistCompletion.model_validate(json_dict)

    tasks_output = getattr(output, "tasks_output", None)
    if tasks_output:
        return _extract(tasks_output[-1])

    raw = getattr(output, "raw", None)
    if isinstance(raw, str) and raw.strip():
        return SpecialistCompletion.model_validate_json(_extract_json_object(raw))

    if isinstance(output, str) and output.strip():
        return SpecialistCompletion.model_validate_json(_extract_json_object(output))

    if isinstance(output, BaseModel):
        return SpecialistCompletion.model_validate(output.model_dump(mode="json"))

    raise SpecialistOutputError("Specialist response did not contain structured completion output.")


def _extract_model(output: Any, model: type[BaseModel]) -> Any:
    if isinstance(output, model):
        return output
    if isinstance(output, Mapping):
        return model.model_validate(output)
    pydantic_output = getattr(output, "pydantic", None)
    if pydantic_output is not None:
        return _extract_model(pydantic_output, model)
    json_dict = getattr(output, "json_dict", None)
    if json_dict is not None:
        return model.model_validate(json_dict)
    tasks_output = getattr(output, "tasks_output", None)
    if tasks_output:
        return _extract_model(tasks_output[-1], model)
    raw = getattr(output, "raw", None)
    if isinstance(raw, str) and raw.strip():
        return model.model_validate_json(_extract_json_object(raw))
    if isinstance(output, str) and output.strip():
        return model.model_validate_json(_extract_json_object(output))
    if isinstance(output, BaseModel):
        return model.model_validate(output.model_dump(mode="json"))
    raise SpecialistOutputError("SEO response did not contain structured completion output.")


def _extract_json_object(raw: str) -> str:
    stripped = raw.strip()
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    start = stripped.find("{")
    if start < 0:
        raise SpecialistOutputError("Specialist response did not contain a JSON object.")
    decoder = json.JSONDecoder()
    _, end = decoder.raw_decode(stripped[start:])
    return stripped[start : start + end]
