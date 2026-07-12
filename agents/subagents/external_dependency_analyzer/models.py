from typing import Literal

from pydantic import BaseModel, Field


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
    line: int | None = None


class ExternalDependencyAnalysis(BaseModel):
    dependencies: list[ExternalDependency] = Field(default_factory=list)
