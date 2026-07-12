"""Structured output schema produced by the entrypoint-documenter subagent."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DependencyRef(BaseModel):
    kind: Literal["database", "cache", "rest_client", "soap_client", "kafka_topic", "other"]
    name: str
    usage: str = Field(description="How/why this entrypoint calls it")


class EntrypointDoc(BaseModel):
    title: str
    type: Literal["REST", "SOAP", "KAFKA"]
    location: str = Field(description="file:line")
    input_model: str = Field(description="Markdown description of the request shape")
    output_model: str = Field(
        description="Markdown description of the response/produced-message shape"
    )
    # Listed before `flow_mermaid` deliberately: structured-output models tend
    # to generate fields in declaration order, and under-invest in whatever
    # comes last. Naming every external call here first also gives the model
    # a checklist to draw from when it then builds the flow diagram.
    dependencies: list[DependencyRef] = Field(default_factory=list)
    flow_mermaid: str = Field(
        description="Mermaid flowchart source: steps, decision points, external calls"
    )
