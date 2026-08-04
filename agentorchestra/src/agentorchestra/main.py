#!/usr/bin/env python

from crewai.flow import Flow, listen, start
from pydantic import BaseModel


class AgentOrchestraStatusState(BaseModel):
    status_message: str = ""


class AgentOrchestraStatusFlow(Flow[AgentOrchestraStatusState]):
    """Status-only Flow entry point kept honest until production orchestration exists."""

    @start()
    def prepare(self, crewai_trigger_payload: dict | None = None) -> None:
        self.state.status_message = (
            "AgentOrchestra Manager routing is available. "
            "Run scripts/run_manager.py for one routing request or "
            "scripts/run_routing_benchmark.py for the approved benchmark. "
            "Specialist execution, staging, QA, and UI orchestration are future phases."
        )

    @listen(prepare)
    def report(self) -> str:
        print(self.state.status_message)
        return self.state.status_message


def kickoff():
    status_flow = AgentOrchestraStatusFlow()
    return status_flow.kickoff()


def plot():
    status_flow = AgentOrchestraStatusFlow()
    status_flow.plot()


def run_with_trigger():
    """
    Run the flow with trigger payload.
    """
    import json
    import sys

    if len(sys.argv) < 2:
        raise Exception("No trigger payload provided. Please provide JSON payload as argument.")

    try:
        trigger_payload = json.loads(sys.argv[1])
    except json.JSONDecodeError as exc:
        raise Exception("Invalid JSON payload provided as argument") from exc

    status_flow = AgentOrchestraStatusFlow()

    try:
        result = status_flow.kickoff({"crewai_trigger_payload": trigger_payload})
        return result
    except Exception as exc:
        raise Exception(f"An error occurred while running the flow with trigger: {exc}") from exc


if __name__ == "__main__":
    kickoff()
