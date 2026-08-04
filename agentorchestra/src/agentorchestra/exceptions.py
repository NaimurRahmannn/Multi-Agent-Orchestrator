class AgentOrchestraError(RuntimeError):
    """Base exception for explicit AgentOrchestra service/helper failures."""


class ConfigurationError(AgentOrchestraError):
    """Raised when operation-specific configuration is missing or unsafe."""


class ManagerExecutionError(AgentOrchestraError):
    """Raised when the live Manager routing operation cannot complete."""


class ManagerOutputError(AgentOrchestraError):
    """Raised when Manager output cannot be converted into the routing contract."""


class DomainValidationError(AgentOrchestraError):
    """Raised by pure domain helpers outside Pydantic input validation."""


class QACoverageError(DomainValidationError):
    """Raised when QA criterion coverage does not match Manager criteria."""
