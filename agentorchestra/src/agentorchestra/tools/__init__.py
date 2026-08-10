"""CrewAI-compatible tools bound to trusted staged-workspace context."""

from agentorchestra.runtime import localize_crewai_paths
from agentorchestra.tools.evidence import PatchEvidenceRecorder
from agentorchestra.tools.workspace_tools import (
    ProposePatchTool,
    ReadFileTool,
    UpdateCSSDeclarationTool,
)

__all__ = [
    "PatchEvidenceRecorder",
    "ProposePatchTool",
    "ReadFileTool",
    "UpdateCSSDeclarationTool",
    "localize_crewai_paths",
]
