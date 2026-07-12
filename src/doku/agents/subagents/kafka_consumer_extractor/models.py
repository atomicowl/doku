"""Structured output owned by the Kafka discovery agent."""

from pydantic import BaseModel, Field


class KafkaConsumer(BaseModel):
    class_name: str
    method_name: str
    file: str
    line: int = Field(ge=1)
    topics: list[str] = Field(default_factory=list)


class KafkaConsumers(BaseModel):
    items: list[KafkaConsumer] = Field(default_factory=list)
