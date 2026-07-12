"""Structured result owned by the entrypoint-documenter subagent."""

from typing import Literal

from pydantic import BaseModel, Field


class DependencyRef(BaseModel):
    kind: Literal[
        "database", "cache", "rest_client", "soap_client", "kafka_producer", "other"
    ]
    name: str
    usage: str = Field(description="How/why this entrypoint calls it")


class EntrypointDoc(BaseModel):
    title: str
    type: str = Field(description="The item kind echoed from the task")
    location: str = Field(description="file:line")
    input_model: str = Field(description="Markdown description of the request shape")
    output_model: str = Field(description="Markdown description of the response shape")
    feature_toggles: list[str] = Field(
        default_factory=list, description="Grounded toggle and enabled/disabled behavior summaries"
    )
    decision_points: list[str] = Field(
        default_factory=list, description="Grounded branch condition and outcome summaries"
    )
    dependencies: list[DependencyRef] = Field(default_factory=list)
    flow_mermaid: str = Field(description="Mermaid flowchart source")
