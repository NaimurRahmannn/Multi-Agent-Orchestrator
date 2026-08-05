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
    """Raised when controlled promotion cannot safely update working."""


class PromotionRollbackError(PromotionError):
    """Raised when promotion rollback cannot be completed or verified."""


class FlowExecutionError(AgentOrchestraError):
    """Raised when the edit Flow reaches an invalid lifecycle state."""
