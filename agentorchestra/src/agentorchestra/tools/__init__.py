"""CrewAI-compatible tools bound to trusted staged-workspace context."""

from agentorchestra.runtime import localize_crewai_paths
from agentorchestra.tools.evidence import PatchEvidenceRecorder
from agentorchestra.tools.workspace_tools import ProposePatchTool, ReadFileTool

__all__ = ["PatchEvidenceRecorder", "ProposePatchTool", "ReadFileTool", "localize_crewai_paths"]

