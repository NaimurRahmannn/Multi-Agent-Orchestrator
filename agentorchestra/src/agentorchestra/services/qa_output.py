from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ValidationError

from agentorchestra.exceptions import QACoverageError, QAOutputError
from agentorchestra.models import QAResult, validate_qa_coverage


def extract_qa_result(
    output: Any,
    *,
    acceptance_criteria: Sequence[str],
    secrets: Sequence[str] = (),
) -> QAResult:
    """Extract supported CrewAI QA output shapes and validate exact criterion coverage."""
    try:
        result = _extract(output)
        validate_qa_coverage(acceptance_criteria, result)
        return result
    except QAOutputError:
        raise
    except (QACoverageError, ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise QAOutputError(
            _safe_message("QA response did not match the structured QA contract.", secrets)
        ) from exc


def _extract(output: Any) -> QAResult:
    if isinstance(output, QAResult):
        return output
    if isinstance(output, Mapping):
        return QAResult.model_validate(output)

    pydantic_output = getattr(output, "pydantic", None)
    if pydantic_output is not None:
        return _extract(pydantic_output)

    json_dict = getattr(output, "json_dict", None)
    if json_dict is not None:
        return QAResult.model_validate(json_dict)

    tasks_output = getattr(output, "tasks_output", None)
    if tasks_output:
        return _extract(tasks_output[-1])

    raw = getattr(output, "raw", None)
    if isinstance(raw, str) and raw.strip():
        return QAResult.model_validate_json(_extract_json_object(raw))

    if isinstance(output, str) and output.strip():
        return QAResult.model_validate_json(_extract_json_object(output))

    if isinstance(output, BaseModel):
        return QAResult.model_validate(output.model_dump(mode="json"))

    raise QAOutputError("QA response did not contain structured QA output.")


def _extract_json_object(raw: str) -> str:
    stripped = raw.strip()
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    start = stripped.find("{")
    if start < 0:
        raise QAOutputError("QA response did not contain a JSON object.")
    decoder = json.JSONDecoder()
    _, end = decoder.raw_decode(stripped[start:])
    return stripped[start : start + end]


def _safe_message(message: str, secrets: Sequence[str]) -> str:
    clean = message
    for secret in secrets:
        if secret:
            clean = clean.replace(secret, "[redacted]")
    return clean
