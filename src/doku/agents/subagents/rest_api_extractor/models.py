"""Structured output owned by the REST discovery agent."""

from pydantic import BaseModel, Field


class RestEndpoint(BaseModel):
    class_name: str
    method_name: str
    file: str
    line: int = Field(ge=1)
    path: str


class RestEndpoints(BaseModel):
    items: list[RestEndpoint] = Field(default_factory=list)
