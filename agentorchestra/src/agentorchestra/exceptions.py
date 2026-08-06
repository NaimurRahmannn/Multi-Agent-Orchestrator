class AgentOrchestraError(RuntimeError):
    """Base exception for explicit AgentOrchestra service/helper failures."""


class ConfigurationError(AgentOrchestraError):
    """Raised when operation-specific configuration is missing or unsafe."""


class ManagerExecutionError(AgentOrchestraError):
    """Raised when the live Manager routing operation cannot complete."""


class ManagerOutputError(AgentOrchestraError):
    """Raised when Manager output cannot be converted into the routing contract."""


class SpecialistExecutionError(AgentOrchestraError):
    """Raised when specialist execution crosses an internal safety boundary."""


class SpecialistOutputError(SpecialistExecutionError):
    """Raised when specialist output cannot be converted into its strict contract."""


class UnsupportedSpecialistError(SpecialistExecutionError):
    """Raised when this stage is asked to execute an unavailable specialist."""


class SpecialistPlanError(SpecialistExecutionError):
    """Raised when a Manager plan is not executable by the HTML/CSS stage."""


class DomainValidationError(AgentOrchestraError):
    """Raised by pure domain helpers outside Pydantic input validation."""


class QACoverageError(DomainValidationError):
    """Raised when QA criterion coverage does not match Manager criteria."""


class QAExecutionError(AgentOrchestraError):
    """Raised when the live QA operation cannot complete."""


class QAOutputError(QAExecutionError):
    """Raised when QA output cannot be converted into the strict QA contract."""


class ExecutionEvidenceError(AgentOrchestraError):
    """Raised when deterministic specialist evidence is inconsistent before QA."""


class PromotionError(AgentOrchestraError):
    """Raised when a site transaction fails, optionally after verified restoration."""

    def __init__(self, message: str, *, working_restored: bool = False) -> None:
        super().__init__(message)
        self.working_restored = working_restored


class PromotionRollbackError(PromotionError):
    """Raised when promotion rollback cannot be completed or verified."""

    def __init__(self, message: str, *, recovery_paths: tuple[str, ...] = ()) -> None:
        super().__init__(message, working_restored=False)
        self.recovery_paths = recovery_paths


class FlowExecutionError(AgentOrchestraError):
    """Raised when the edit Flow reaches an invalid lifecycle state."""


class ScreenshotSafetyError(AgentOrchestraError):
    """Raised when screenshot capture detects an unsafe or corrupted path boundary."""
