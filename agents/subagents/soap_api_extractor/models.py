"""Structured output owned by the SOAP discovery agent."""

from pydantic import BaseModel, Field


class SoapOperation(BaseModel):
    class_name: str
    method_name: str
    file: str
    line: int = Field(ge=1)
    namespace: str | None = None
    operation: str
    soap_action: str | None = None


class SoapOperations(BaseModel):
    items: list[SoapOperation] = Field(default_factory=list)
