"""Structured result owned by the entrypoint-documenter subagent."""

from typing import Literal

from pydantic import BaseModel, Field


class DependencyRef(BaseModel):
    kind: Literal["database", "cache", "rest_client", "soap_client", "kafka_topic", "other"]
    name: str
    usage: str = Field(description="How/why this entrypoint calls it")


class EntrypointDoc(BaseModel):
    title: str
    type: str = Field(description="The item kind echoed from the task")
    location: str = Field(description="file:line")
    input_model: str = Field(description="Markdown description of the request shape")
    output_model: str = Field(description="Markdown description of the response shape")
    dependencies: list[DependencyRef] = Field(default_factory=list)
    flow_mermaid: str = Field(description="Mermaid flowchart source")
