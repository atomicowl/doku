from typing import Literal

from pydantic import BaseModel, Field


class DecisionPoint(BaseModel):
    condition: str
    location: str | None = Field(
        default=None, description="Repository-relative file:line in the call chain"
    )
    true_path: str
    false_path: str | None = None


class FeatureToggle(BaseModel):
    name: str
    source: str = Field(description="Flag service, annotation, property, or environment lookup")
    location: str | None = Field(
        default=None, description="Repository-relative file:line in the call chain"
    )
    enabled_behavior: str
    disabled_behavior: str | None = None


class ExternalDependency(BaseModel):
    kind: Literal[
        "database", "cache", "rest_client", "soap_client", "kafka_producer", "other"
    ]
    name: str
    operation: str | None = None
    destination: str | None = Field(
        default=None, description="REST URL/path, SOAP operation, Kafka topic, or datastore"
    )
    usage: str
    location: str | None = Field(
        default=None, description="Repository-relative file:line in the call chain"
    )


class CallChainAnalysis(BaseModel):
    call_chain: list[str] = Field(
        default_factory=list,
        description="Ordered repository-relative methods traversed from the entrypoint",
    )
    decision_points: list[DecisionPoint] = Field(default_factory=list)
    feature_toggles: list[FeatureToggle] = Field(default_factory=list)
    dependencies: list[ExternalDependency] = Field(default_factory=list)
    flow_mermaid: str = Field(description="A flowchart TD diagram of the reachable call chain")
