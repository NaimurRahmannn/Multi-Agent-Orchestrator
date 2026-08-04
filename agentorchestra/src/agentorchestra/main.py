#!/usr/bin/env python

from pydantic import BaseModel

from crewai.flow import Flow, listen, start


class PhaseOneState(BaseModel):
    readiness_message: str = ""


class PhaseOneFlow(Flow[PhaseOneState]):
    """Minimal generated Flow entry point kept runnable for Phase 1."""

    @start()
    def prepare(self, crewai_trigger_payload: dict | None = None) -> None:
        self.state.readiness_message = (
            "AgentOrchestra Phase 1 foundation is installed. "
            "Run scripts/feasibility checks for live environment evidence."
        )

    @listen(prepare)
    def report(self) -> str:
        print(self.state.readiness_message)
        return self.state.readiness_message


def kickoff():
    phase_one_flow = PhaseOneFlow()
    return phase_one_flow.kickoff()


def plot():
    phase_one_flow = PhaseOneFlow()
    phase_one_flow.plot()


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
    except json.JSONDecodeError:
        raise Exception("Invalid JSON payload provided as argument")

    phase_one_flow = PhaseOneFlow()

    try:
        result = phase_one_flow.kickoff({"crewai_trigger_payload": trigger_payload})
        return result
    except Exception as e:
        raise Exception(f"An error occurred while running the flow with trigger: {e}")


if __name__ == "__main__":
    kickoff()
