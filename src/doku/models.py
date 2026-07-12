"""Structured output schemas produced by the subagents."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class DiscoveredEntrypoint(BaseModel):
    """One entrypoint found by a discovery subagent."""

    type: Literal["REST", "SOAP", "KAFKA"]
    file: str = Field(description="Path relative to the repo root, forward-slash separated, no leading /repo/")
    line: int = Field(description="1-based line number of the handler method declaration")
    class_name: str
    method_name: str
    meta: dict[str, Any] = Field(
        default_factory=dict,
        description="Framework specifics: e.g. HTTP method + route, Kafka topics + group id, SOAP namespace/operation",
    )


class DiscoveredEntrypoints(BaseModel):
    """A discovery subagent's full answer for its entrypoint type."""

    entrypoints: list[DiscoveredEntrypoint] = Field(default_factory=list)


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
