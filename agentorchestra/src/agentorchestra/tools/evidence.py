from __future__ import annotations

from dataclasses import dataclass, field

from agentorchestra.workspace_models import PatchExecutionResult


@dataclass(slots=True)
class PatchEvidenceRecorder:
    """Per-run trusted sink for actual patch-tool service results."""

    _results: list[PatchExecutionResult] = field(default_factory=list, init=False, repr=False)

    def record(self, result: PatchExecutionResult) -> None:
        self._results.append(PatchExecutionResult.model_validate(result))

    def snapshot(self) -> tuple[PatchExecutionResult, ...]:
        return tuple(self._results)
