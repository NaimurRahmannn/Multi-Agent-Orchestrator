from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from agentorchestra.config import Settings
from agentorchestra.models import SpecialistName
from agentorchestra.observability_models import (
    RunTimeline,
    TimelineEvent,
    TimelineEventStatus,
    TimelineStage,
)
from agentorchestra.path_safety import redact_absolute_path_text, redact_secret_like_text


@dataclass(frozen=True)
class TimelineToken:
    sequence: int
    stage: TimelineStage
    specialist: SpecialistName | None
    started_at: float
    started_offset_ms: float


class RunTimelineRecorder:
    """Per-run recorder for safe lifecycle events derived at execution time."""

    def __init__(
        self,
        *,
        run_id: str | None = None,
        clock: Callable[[], float] = time.perf_counter,
        settings: Settings | None = None,
    ) -> None:
        self._clock = clock
        self._origin = clock()
        self._run_id = run_id
        self._settings = settings
        self._events: list[TimelineEvent] = []
        self._next_sequence = 0

    def set_run_id(self, run_id: str) -> None:
        if self._run_id is not None and self._run_id != run_id:
            raise ValueError("Timeline run ID cannot change.")
        self._run_id = run_id

    def start(
        self,
        stage: TimelineStage,
        *,
        specialist: SpecialistName | None = None,
    ) -> TimelineToken:
        now = self._clock()
        token = TimelineToken(
            sequence=self._next_sequence,
            stage=stage,
            specialist=specialist,
            started_at=now,
            started_offset_ms=float(max(0.0, (now - self._origin) * 1000)),
        )
        self._next_sequence += 1
        return token

    def finish(
        self,
        token: TimelineToken,
        *,
        status: TimelineEventStatus,
        message: str,
        duration_ms: float | None = None,
    ) -> TimelineEvent:
        if any(event.sequence == token.sequence for event in self._events):
            raise ValueError("Timeline token was already finished.")
        duration = (
            float(max(0.0, (self._clock() - token.started_at) * 1000))
            if duration_ms is None
            else float(max(0.0, duration_ms))
        )
        event = TimelineEvent(
            sequence=token.sequence,
            stage=token.stage,
            status=status,
            specialist=token.specialist,
            started_offset_ms=token.started_offset_ms,
            duration_ms=duration,
            message=self._safe_message(message),
        )
        self._events.append(event)
        self._events.sort(key=lambda item: item.sequence)
        return event.model_copy(deep=True)

    def record(
        self,
        stage: TimelineStage,
        *,
        status: TimelineEventStatus,
        message: str,
        specialist: SpecialistName | None = None,
        duration_ms: float = 0.0,
    ) -> TimelineEvent:
        return self.finish(
            self.start(stage, specialist=specialist),
            status=status,
            message=message,
            duration_ms=duration_ms,
        )

    def snapshot(self) -> RunTimeline:
        now = self._clock()
        event_end = max(
            (event.started_offset_ms + event.duration_ms for event in self._events),
            default=0.0,
        )
        elapsed = float(max(0.0, (now - self._origin) * 1000))
        return RunTimeline(
            run_id=self._run_id,
            events=[event.model_copy(deep=True) for event in self._events],
            total_observed_duration_ms=max(elapsed, event_end),
        )

    def _safe_message(self, message: str) -> str:
        clean = str(message).replace("\n", " ").strip()
        if self._settings is not None:
            clean = clean.replace(str(self._settings.project_root), "[project]")
            for secret in self._settings.groq_api_key_values:
                clean = clean.replace(secret, "[redacted]")
        clean = redact_secret_like_text(redact_absolute_path_text(clean))
        return clean[:500] or "Lifecycle stage completed."
